# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from dataclasses import dataclass
import sqlite3
from typing import List, Sequence, Tuple

from npusim.prof_demo.config import config
from npusim.prof_demo.database import database_handler
from npusim.prof_demo.utils import edge_instructions


VF_CLASS_SCALAR = "SCALAR"
VF_CLASS_EXECUTION = "EXECUTION"
VF_CLASS_LOAD_STORE = "LOAD_STORE"
VF_CLASS_BRANCH = "BRANCH"


class VfCallAnalyzerBase:
    """Query and static-IPC helpers shared by VF/VFI (SIMD) and VF_SIMT calls."""

    VF_NAMES: Tuple[str, ...] = ()
    CLASS_COLUMN = ""
    CLASS_IDS: Tuple[str, ...] = ()
    EXCLUDED_INSTRS: Tuple[str, ...] = ()

    @classmethod
    def iter_vfs(cls):
        """Yield (vf_instr_id, instructions nested in the call) in legacy order."""
        supported_instrs_str = str(cls.VF_NAMES).replace(",)", ")")
        vf_instrs = database_handler.execute(
            """
            SELECT * FROM ExecutedInstructions
            WHERE ExecutedInstructions.ExecInstrName IN {0}
            ORDER BY ExecInstrCoreId, CoreTypeId, ExecInstrTickStart
        """.format(supported_instrs_str)
        ).fetchall()
        supported_classes = str(cls.CLASS_IDS).replace(",)", ")")
        excluded = ""
        if cls.EXCLUDED_INSTRS:
            excluded = "AND EI.ExecInstrName NOT IN ({0})".format(",".join(f"'{n}'" for n in cls.EXCLUDED_INSTRS))
        query = """
            SELECT * FROM ExecutedInstructions AS EI
            WHERE
                EI.ExecInstrCoreId = :core_id
                AND EI.CoreTypeId = :core_type_id
                AND EI.{0} IN {1}
                AND EI.ExecInstrTickStart >= :tick_start
                AND EI.ExecInstrTickEnd <= :tick_end
                {2}
            ORDER BY ExecInstrTickStart, ExecInstrTickEnd
        """.format(cls.CLASS_COLUMN, supported_classes, excluded)
        for vf_instr in vf_instrs:
            params = {
                "core_id": int(vf_instr["ExecInstrCoreId"]),
                "core_type_id": int(vf_instr["CoreTypeId"]),
                "tick_start": int(vf_instr["ExecInstrTickStart"]),
                "tick_end": int(vf_instr["ExecInstrTickEnd"]),
            }
            yield int(vf_instr["ExecInstrId"]), database_handler.execute(query, params).fetchall()

    @classmethod
    def count_instrs_by_group(cls, instrs_of_vf: Sequence[sqlite3.Row]) -> List[int]:
        class_to_group = {instr_class: i for i, instr_class in enumerate(cls.CLASS_IDS)}
        counts = [0] * len(cls.CLASS_IDS)
        for instr in instrs_of_vf:
            idx = class_to_group.get(instr[cls.CLASS_COLUMN])
            if idx is not None:
                counts[idx] += 1
        return counts

    @classmethod
    def static_ipc(cls, instrs_of_vf: Sequence[sqlite3.Row]) -> List[float]:
        difference = int(instrs_of_vf[-1]["ExecInstrTickEnd"]) - int(instrs_of_vf[0]["ExecInstrTickStart"])
        cycles = max(difference, 1)
        return [count / cycles for count in cls.count_instrs_by_group(instrs_of_vf)]


@dataclass(frozen=True)
class VfIPCItem:
    exec_instr_id: int
    window_start_tick: int
    scalar_ipc: float
    exec_ipc: float
    ldst_ipc: float

    def as_tuple(self) -> Tuple[int, int, float, float, float]:
        return (self.exec_instr_id, self.window_start_tick, self.scalar_ipc, self.exec_ipc, self.ldst_ipc)


class Ascend950VFIPCAnalyzer(VfCallAnalyzerBase):
    VF_NAMES = ("VF", "VFI")
    CLASS_COLUMN = "ExecInstrVfClass"
    CLASS_IDS = (VF_CLASS_SCALAR, VF_CLASS_EXECUTION, VF_CLASS_LOAD_STORE)

    def process_vf_dynamic(self, instrs_of_vf: List[sqlite3.Row], sliding_window_size: int) -> List[VfIPCItem]:
        # Sliding window IPC calculation for vector function instructions.
        #
        # Window of N instrs slides by 1. At each position, counts
        # scalar/exec/ldst instructions within the window, divided
        # by the tick span (time delta) to get instructions-per-cycle.
        #
        #   [====window(N=6)====]
        #    scalar:2  exec:5  ldst:3
        #   tick_start=101  tick_end=105  delta=4 ticks
        #   exec_ipc = 5/4 = 1.25
        #
        # Edge instructions (outside full windows) inherit the nearest
        # window's IPC values (imputation for visualization continuity).
        result: List[VfIPCItem] = []

        # If the number of instructions is smaller,
        # the window shrinks to fit
        sliding_window_size_actual = (
            sliding_window_size if len(instrs_of_vf) >= sliding_window_size else len(instrs_of_vf)
        )

        # Sliding window moves as i increases
        for i in range(0, len(instrs_of_vf) - sliding_window_size_actual + 1):
            i_start = i
            i_mid = i + int(sliding_window_size_actual / 2)
            i_end = i + sliding_window_size_actual - 1

            w_start_tick = instrs_of_vf[i_start]["ExecInstrTickStart"]
            w_end_tick = instrs_of_vf[i_end]["ExecInstrTickEnd"]
            w_ticks = max(1, w_end_tick - w_start_tick)

            w_scalar_num, w_exec_num, w_ldst_num = self.count_instrs_by_group(instrs_of_vf[i_start : i_end + 1])

            result.append(
                VfIPCItem(
                    instrs_of_vf[i_mid]["ExecInstrId"],
                    w_start_tick,
                    w_scalar_num / w_ticks,
                    w_exec_num / w_ticks,
                    w_ldst_num / w_ticks,
                )
            )

        start_instrs, end_instrs = edge_instructions(instrs_of_vf, sliding_window_size_actual)
        first, last = result[0], result[-1]
        start_values = [
            VfIPCItem(i["ExecInstrId"], first.window_start_tick, first.scalar_ipc, first.exec_ipc, first.ldst_ipc)
            for i in start_instrs
        ]
        end_values = [
            VfIPCItem(i["ExecInstrId"], last.window_start_tick, last.scalar_ipc, last.exec_ipc, last.ldst_ipc)
            for i in end_instrs
        ]
        return start_values + result + end_values

    def process(self) -> None:
        for vf_instr_id, instrs_of_vf in self.iter_vfs():
            # cannsim_helper skips a VF without classified nested
            # instructions; the dynamic path also requires a non-empty window.
            if not instrs_of_vf:
                continue
            database_handler.execute(
                "INSERT INTO main.VfIPC (ExecInstrId, ScalarIPC, ExecIPC, LdStIPC) VALUES (?, ?, ?, ?)",
                (vf_instr_id, *self.static_ipc(instrs_of_vf)),
            )
            database_handler.executemany(
                """
            INSERT OR IGNORE INTO main.VfIPCDynamic
                (ExecInstrId, WindowStartTick, ScalarIPC, ExecIPC, LdstIPC)
            VALUES
                (?, ?, ?, ?, ?)
            """,
                [item.as_tuple() for item in self.process_vf_dynamic(instrs_of_vf, config.sliding_window_vec)],
            )
