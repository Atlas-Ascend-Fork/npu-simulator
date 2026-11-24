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


import sqlite3
from typing import List, Sequence, Tuple

from npusim.prof_demo.database import database_handler


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


class Ascend950VFIPCAnalyzer(VfCallAnalyzerBase):
    VF_NAMES = ("VF", "VFI")
    CLASS_COLUMN = "ExecInstrVfClass"
    CLASS_IDS = (VF_CLASS_SCALAR, VF_CLASS_EXECUTION, VF_CLASS_LOAD_STORE)

    def process(self) -> None:
        for vf_instr_id, instrs_of_vf in self.iter_vfs():
            # cannsim_helper skips VFs without classified nested instructions.
            if not instrs_of_vf:
                continue
            database_handler.execute(
                "INSERT INTO main.VfIPC (ExecInstrId, ScalarIPC, ExecIPC, LdStIPC) VALUES (?, ?, ?, ?)",
                (vf_instr_id, *self.static_ipc(instrs_of_vf)),
            )
