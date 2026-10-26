# -*- coding: utf-8 -*-
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
from typing import List, Dict, Set, Tuple, Iterator

from npusim.prof.src.common.biprof_constants import VfInstrTypes
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.vf_ipc_analyzer_base import (
    Ascend950VfIPCAnalyzerBase,
    VfRawData,
    VfIPCItemBase,
)


# vf_exec_instr_id -> (Scalar IPC, Exec IPC, Load/Store IPC)
# (3 different IPC values per VF)
VfMetrics = Dict[int, Tuple[float, float, float]]


@dataclass(frozen=True)
class VfIPCItem(VfIPCItemBase):
    scalar_ipc: float
    exec_ipc: float
    ldst_ipc: float

    def as_vf_ipc_dynamic_tuple(self) -> Tuple[int, int, float, float, float]:
        return (
            self.exec_instr_id,
            self.window_start_tick,
            self.scalar_ipc,
            self.exec_ipc,
            self.ldst_ipc,
        )

    def as_instr_ipc_tuple(self) -> Tuple[int, float, float, float]:
        if self.instr_type in VfInstrTypes["Scalar"]:
            ipcs = (self.scalar_ipc, 0.0, 0.0)
        elif self.instr_type in VfInstrTypes["Execution"]:
            ipcs = (0.0, self.exec_ipc, 0.0)
        else:  # self.instr_type in VfInstrTypes["Load/Store"]
            ipcs = (0.0, 0.0, self.ldst_ipc)
        return (self.exec_instr_id, *ipcs)


class Ascend950VFIPCAnalyzer(Ascend950VfIPCAnalyzerBase):
    @staticmethod
    def get_name() -> str:
        return "ascend950_vf_ipc_analyzer"

    @staticmethod
    def get_supported_vfs() -> List[str]:
        return [
            "VF",
            "VFI",
        ]

    @staticmethod
    def get_supported_groups() -> Dict[str, Set[InstrType]]:
        return VfInstrTypes

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

        if not instrs_of_vf:
            return result

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

            w_mid_instr_id = instrs_of_vf[i_mid]["ExecInstrId"]
            w_mid_instr_type = InstrType(int(instrs_of_vf[i_mid]["InstrTypeId"]))

            scalar_ipc = w_scalar_num / w_ticks
            exec_ipc = w_exec_num / w_ticks
            ldst_ipc = w_ldst_num / w_ticks

            result.append(
                VfIPCItem(
                    w_mid_instr_id,
                    w_mid_instr_type,
                    w_start_tick,
                    scalar_ipc,
                    exec_ipc,
                    ldst_ipc,
                )
            )

        start_values, end_values = self.get_edge_values(instrs_of_vf, result[0], result[-1], sliding_window_size_actual)
        result = start_values + result
        result.extend(end_values)

        return result

    @staticmethod
    def get_edge_values(
        instructions: List[sqlite3.Row],
        item_start: VfIPCItem,
        item_end: VfIPCItem,
        sliding_window_size_actual: int,
    ) -> Tuple[List[VfIPCItem], List[VfIPCItem]]:
        start_items, end_items = [], []

        # Retrieve instructions left uncovered at the start and end
        start_instructions, end_instructions = [], []
        if len(instructions) >= 2:
            start_instructions = instructions[: int(sliding_window_size_actual / 2)]
        if len(instructions) > 2:
            is_even = sliding_window_size_actual % 2 == 0
            end_instructions = instructions[-(int(sliding_window_size_actual / 2) - is_even) :]

        # Generate edge values
        for instr in start_instructions:
            start_items.append(
                VfIPCItem(
                    instr["ExecInstrId"],
                    InstrType(int(instr["InstrTypeId"])),
                    item_start.window_start_tick,
                    item_start.scalar_ipc,
                    item_start.exec_ipc,
                    item_start.ldst_ipc,
                )
            )
        for instr in end_instructions:
            end_items.append(
                VfIPCItem(
                    instr["ExecInstrId"],
                    InstrType(int(instr["InstrTypeId"])),
                    item_end.window_start_tick,
                    item_end.scalar_ipc,
                    item_end.exec_ipc,
                    item_end.ldst_ipc,
                )
            )

        return start_items, end_items

    @staticmethod
    def save_vf_metrics(vf_metrics: VfMetrics) -> None:
        database_handler.executemany(
            """
        INSERT INTO VfIPC
            (ExecInstrId, ScalarIPC, ExecIPC, LdStIPC)
        VALUES
            (?, ?, ?, ?)
        """,
            [(vf_id, *ipc_values) for vf_id, ipc_values in vf_metrics.items()],
        )

    @staticmethod
    def save_vf_ipc_dynamic(data: List[VfIPCItem]) -> None:
        database_handler.executemany(
            """
        INSERT OR IGNORE INTO VfIPCDynamic
            (ExecInstrId,
             WindowStartTick,
             ScalarIPC,
             ExecIPC,
             LdstIPC)
        VALUES
            (?, ?, ?, ?, ?)
        """,
            [item.as_vf_ipc_dynamic_tuple() for item in data],
        )
        database_handler.executemany(
            """
        INSERT OR IGNORE INTO VfIPCInside
            (ExecInstrId,
             ScalarIPC,
             ExecIPC,
             LdstIPC)
        VALUES
            (?, ?, ?, ?)
        """,
            [item.as_instr_ipc_tuple() for item in data],
        )

    def process_vfs(self, sliding_window_size: int, vf_raw_data: Iterator[VfRawData]) -> None:
        for vf_item in vf_raw_data:
            vf_metrics: VfMetrics = dict()
            vf_dynamic_data: List[VfIPCItem] = []

            vf_instr_id = vf_item.vf_instr_id
            instrs_of_vf = vf_item.instrs_of_vf

            # 1. Count instrs per group
            scalar_num, exec_num, ldst_num = self.count_instrs_by_group(instrs_of_vf)

            # 2. Calculate 3 static IPC metrics
            vf_metrics[vf_instr_id] = (
                self.calc_vf_ipc_static(instrs_of_vf, scalar_num),
                self.calc_vf_ipc_static(instrs_of_vf, exec_num),
                self.calc_vf_ipc_static(instrs_of_vf, ldst_num),
            )

            # 3. Calculate dynamic VF IPC data
            vf_dynamic_data = self.process_vf_dynamic(instrs_of_vf, sliding_window_size)

            self.save_vf_metrics(vf_metrics)
            self.save_vf_ipc_dynamic(vf_dynamic_data)
