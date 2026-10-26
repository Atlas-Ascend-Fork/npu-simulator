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

from typing import Any

from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


class Ascend950ScalarInstructionPerCycleAnalyzer(IAnalyzer):
    @staticmethod
    def get_name() -> str:
        return "ascend950_scalar_ipc_analyzer"

    @staticmethod
    def make_indices() -> None:
        database_handler.execute("""
        CREATE INDEX IF NOT EXISTS idx_ei_scalar_ipc_covering
        ON ExecutedInstructions(
            ExecInstrCoreId, CoreTypeId,
            InstrTypeId, ExecInstrId, ExecInstrTickStart
        )
        """)
        database_handler.execute("""
        CREATE INDEX IF NOT EXISTS idx_dt_exec_instr_tick
        ON DispatchTime(ExecInstrId, DispatchTimeTick)
        """)

    @staticmethod
    def drop_indices() -> None:
        database_handler.execute("DROP INDEX IF EXISTS idx_ei_scalar_ipc_covering")
        database_handler.execute("DROP INDEX IF EXISTS idx_dt_exec_instr_tick")

    @staticmethod
    def get_stats(core_id: int, core_type_id: int) -> Any:
        return database_handler.execute(
            """
        SELECT COUNT() AS InstrCount,
        MAX(COALESCE(DispatchTimeTick, ExecInstrTickStart)) -
        MIN(COALESCE(DispatchTimeTick, ExecInstrTickStart))
        AS TickDifference FROM ExecutedInstructions
        LEFT JOIN DispatchTime
        ON ExecutedInstructions.ExecInstrId =
           DispatchTime.ExecInstrId
        WHERE ExecInstrCoreId = {}
        AND CoreTypeId = {}
        AND InstrTypeId NOT IN {}
        """.format(
                core_id,
                core_type_id,
                tuple(
                    [
                        InstrType.RVECEX.value,
                        InstrType.RVECLP.value,
                        InstrType.RVECSU.value,
                        InstrType.RVECLD.value,
                        InstrType.RVECST.value,
                    ]
                ),
            )
        ).fetchall()[0]

    @staticmethod
    def save_ipc_metric_value(ipc: float) -> None:
        database_handler.execute(
            """
        INSERT INTO KernelInfo (KernelInfoAttr, KernelInfoVal)
        VALUES
        (?, ?)
        """,
            ("scalar_ipc", ipc),
        )

    def process(self) -> None:
        self.make_indices()
        ipc_per_core = []
        for core_id in self.get_active_cores():
            ipc_per_subcore = []
            for core_type_id in self.get_active_core_types():
                stats = self.get_stats(core_id, core_type_id)
                dispatched_instrs_count = stats["InstrCount"]
                cycles = stats["TickDifference"]
                if not cycles:
                    continue
                ipc = dispatched_instrs_count / cycles
                ipc_per_subcore.append(ipc)
            ipc_per_core.append(sum(ipc_per_subcore) / len(ipc_per_subcore))
        ipc = round(sum(ipc_per_core) / len(ipc_per_core), 2)
        self.save_ipc_metric_value(ipc)
        self.drop_indices()
