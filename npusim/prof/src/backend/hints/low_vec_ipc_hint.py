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

import sqlite3
from typing import Dict, List, Optional

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.hints.base.hint_base import (
    HintDataItem,
    HintTarget,
    IHint,
)


class LowVecIpcHint(IHint):
    # Kernel-level hint: fires when the mean ExecIPC across VF (vector)
    # instructions is low, indicating that the VEC pipe is
    # instruction-starved (low instruction-per-clock ratio on VEC).
    #
    # The VfIPC table holds per-instruction IPC counters for VF (PUSHQ)
    # instructions on AIV cores. No InstrType/CoreType filtering is needed
    # because VfIPC only contains rows for those instructions.
    #
    # Distinct from VectorUtilizationHint, which is a per-instruction hint
    # based on the VectorUtilization column (vector lane utilization).
    # This hint aggregates VfIPC.ExecIPC across the whole kernel to flag
    # a kernel-level VEC IPC problem.
    #
    # Routes into the KernelHints table via is_kernel=True.

    IPC_THRESHOLD = 0.5

    @staticmethod
    def get_name() -> str:
        return "low_vec_ipc_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        cursor = database_handler.execute("SELECT ExecIPC FROM VfIPC")
        rows = cursor.fetchall()
        if not rows:
            return self.result
        ipc_values = [float(r[0]) for r in rows if r[0] is not None]
        if not ipc_values:
            return self.result
        mean_ipc = round(sum(ipc_values) / len(ipc_values), 4)
        if mean_ipc >= self.IPC_THRESHOLD:
            return self.result

        self.add_hint(
            f"Low VEC IPC: mean ExecIPC={mean_ipc} across {len(ipc_values)} "
            f"VF instructions (threshold {self.IPC_THRESHOLD}). The vector "
            "pipe is instruction-starved. Consider increasing the vector "
            "instruction count, reducing stalls, or improving "
            "instruction-level parallelism.",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
