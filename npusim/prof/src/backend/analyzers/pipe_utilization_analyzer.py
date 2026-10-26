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
from dataclasses import dataclass
from typing import List

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.exceptions import RequirementsIncomplete
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import WAIT_INSTRS
import npusim.prof.src.common.biprof_enum as enums

InstrQueueType = enums.InstrQueueType
queue_type_to_instr_types = enums.queue_type_to_instr_types()


@dataclass
class PipeUtilInfo:
    core_id: int
    core_type_id: int
    instr_queue_type: InstrQueueType
    utilization_value: float


class PipeUtilizationAnalyzer(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.utilizations: List[PipeUtilInfo] = []
        self.kernel_exec_time_ticks: int = 0

    @staticmethod
    def get_name() -> str:
        return "pipe_utilization"

    @staticmethod
    def make_index() -> None:
        database_handler.execute("""
        CREATE INDEX IF NOT EXISTS idx_ei_pipe_util_covering
        ON ExecutedInstructions(
            ExecInstrCoreId, CoreTypeId, InstrTypeId, ExecInstrName,
            ExecInstrTickStart
        )
        """)

    @staticmethod
    def drop_index() -> None:
        database_handler.execute("DROP INDEX IF EXISTS idx_ei_pipe_util_covering")

    @staticmethod
    def get_pipe_instrs(core_id: int, core_type_id: int, instr_queue_type) -> List[sqlite3.Row]:
        executed_instrs = None
        instr_type_ids = [e.value for e in queue_type_to_instr_types[instr_queue_type] if e != enums.InstrType.FC]
        instr_types_str = f"({', '.join(map(str, instr_type_ids))})"
        executed_instrs = database_handler.execute(
            """
            SELECT ExecInstrTickStart, ExecInstrTickEnd
            FROM ExecutedInstructions
            WHERE ExecInstrCoreId = :core_id AND CoreTypeId = :core_type_id
                  AND InstrTypeId IN {0}
                  AND ExecInstrName NOT IN {1}
                  AND ExecInstrName NOT LIKE 'VF%'
                  AND ExecInstrName NOT LIKE 'DFX_REGION%'
            ORDER BY ExecInstrTickStart
            """.format(instr_types_str, WAIT_INSTRS),
            {"core_id": core_id, "core_type_id": core_type_id},
        )
        result: List[sqlite3.Row] = executed_instrs.fetchall()
        return result

    @staticmethod
    def save_utilizations(utilizations: List[PipeUtilInfo]) -> None:
        database_handler.executemany(
            """
        INSERT INTO PipesUtilization
        (CoreId, CoreTypeId, InstrQueueTypeId, PipeUtilization)
        VALUES
        (?, ?, ?, ?)
        """,
            [
                (
                    util_info.core_id,
                    util_info.core_type_id,
                    util_info.instr_queue_type.value,
                    util_info.utilization_value,
                )
                for util_info in utilizations
            ],
        )

    @staticmethod
    def calc_utilization(pipe_instrs: List[sqlite3.Row], kernel_exec_time_ticks: int) -> float:
        if not pipe_instrs:
            return 0.0

        start = pipe_instrs[0]["ExecInstrTickStart"]
        end = pipe_instrs[0]["ExecInstrTickEnd"]
        gaps_total_time = 0.0

        # Assume gaps time between instructions
        for instr in pipe_instrs:
            # Instructions in pipe_instrs are sorted by ExecInstrTickStart.
            # [start, end] here always represents
            # some continious segment of instrs.
            # So, we have 3 cases how [start, end] segment and instr segment
            # can be relatively located
            if instr["ExecInstrTickStart"] >= start and instr["ExecInstrTickStart"] <= end:
                # Case 1:
                # > start-> [   ] <- end
                # >          [ ]
                if instr["ExecInstrTickEnd"] <= end:
                    continue
                # Case 2:
                # > start-> [   ] <- end
                # >           [   ]
                else:
                    end = instr["ExecInstrTickEnd"]
            # Case 3:
            # > start-> [   ] <-end
            # >                [   ]
            else:  # instr["ExecInstrTickStart"] > end
                gaps_total_time += instr["ExecInstrTickStart"] - end
                start = instr["ExecInstrTickStart"]
                end = instr["ExecInstrTickEnd"]

        # Assume gaps time at the edges
        left_edge = pipe_instrs[0]["ExecInstrTickStart"]
        right_edge = max(pipe_instrs, key=lambda instr: instr["ExecInstrTickEnd"])["ExecInstrTickEnd"]
        return float(((right_edge - left_edge) - gaps_total_time) / kernel_exec_time_ticks * 100)

    def check_requirements(self) -> None:
        if not database_handler.count_rows("ExecutedInstructions"):
            raise RequirementsIncomplete("No executed instructions found")

    def process_single_pipe(self, core_id: int, core_type_id: int, instr_queue_type: InstrQueueType) -> PipeUtilInfo:
        pipe_instrs = self.get_pipe_instrs(core_id, core_type_id, instr_queue_type)
        utilization_value = self.calc_utilization(pipe_instrs, self.kernel_exec_time_ticks)
        return PipeUtilInfo(
            core_id=core_id,
            core_type_id=core_type_id,
            instr_queue_type=instr_queue_type,
            utilization_value=utilization_value,
        )

    def process(self) -> None:
        self.make_index()
        self.kernel_exec_time_ticks = database_handler.get_exec_time_ticks()

        for core_id in self.get_active_cores():
            for core_type_id in self.get_active_core_types():
                for instr_queue_type in queue_type_to_instr_types.keys():
                    util_info = self.process_single_pipe(core_id, core_type_id, instr_queue_type)
                    self.utilizations.append(util_info)
        self.save_utilizations(self.utilizations)
        self.drop_index()

    def cleanup(self) -> None:
        self.utilizations.clear()
