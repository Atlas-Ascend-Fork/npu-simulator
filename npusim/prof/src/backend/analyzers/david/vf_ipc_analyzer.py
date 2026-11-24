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

from typing import List, Dict, Set, Tuple, Iterator

from npusim.prof.src.common.biprof_constants import VfInstrTypes
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.vf_ipc_analyzer_base import (
    Ascend950VfIPCAnalyzerBase,
    VfRawData,
)


# vf_exec_instr_id -> (Scalar IPC, Exec IPC, Load/Store IPC)
# (3 different IPC values per VF)
VfMetrics = Dict[int, Tuple[float, float, float]]


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

    def process_vfs(self, vf_raw_data: Iterator[VfRawData]) -> None:
        for vf_item in vf_raw_data:
            vf_metrics: VfMetrics = dict()

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

            self.save_vf_metrics(vf_metrics)
