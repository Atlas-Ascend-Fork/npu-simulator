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
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import CoreType
from npusim.prof.src.common.biprof_constants import (
    SAMPLING_PIPES_TO_SUBCORES,
    SYNCHRO_INSTRS,
)
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


PUSH = 0
POP = 1
RETIRE = 2


@dataclass
class SubqueueUtilEvent:
    tick: int
    util: int
    core_id: int
    core_type_id: int
    pipe_id: int


class Ascend950SubqueueUtilizationAnalyzer(IAnalyzer):
    # Per-pipe issue/exec queue depth tracking over time.
    # For each compute pipe (MTE1, MTE2, CUBE, etc.), tracks queue occupancy
    # via a state machine: PUSH (dispatch into issue queue), POP (move from
    # issue to exec queue), RETIRE (complete from exec queue).
    # Skips flag/synchronization instructions that bypass normal queuing.
    # Produces IssueQueueUtilization and ExecQueueUtilization time series.
    skip_flag_instrs: List[str] = [
        "SET_FLAG",
        "RLS_BUF",
        "SET_CROSS_CORE",
        "SET_INTRA_BLOCK",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.exec: List[SubqueueUtilEvent] = []
        self.issue: List[SubqueueUtilEvent] = []

    @staticmethod
    def get_name() -> str:
        return "ascend950_subqueue_utilization_analyzer"

    @staticmethod
    def make_indices() -> None:
        database_handler.execute("""
        CREATE INDEX IF NOT EXISTS idx_ei_subq_util_covering
        ON ExecutedInstructions(
            ExecInstrCoreId, CoreTypeId, InstrTypeId, ExecInstrTickStart
        )
        """)
        database_handler.execute("""
        CREATE INDEX IF NOT EXISTS idx_dt_exec_instr_tick
        ON DispatchTime(ExecInstrId, DispatchTimeTick)
        """)

    @staticmethod
    def drop_indices() -> None:
        database_handler.execute("DROP INDEX IF EXISTS idx_ei_subq_util_covering")
        database_handler.execute("DROP INDEX IF EXISTS idx_dt_exec_instr_tick")

    @staticmethod
    def get_pipe_instrs(core_id: int, core_type_id: int, pipe: int) -> List[sqlite3.Row]:
        return database_handler.execute(
            f"""
        SELECT ExecutedInstructions.ExecInstrId AS ExecInstrId,
        DispatchTimeTick AS Tick, {PUSH} AS State, ExecInstrName
        FROM DispatchTime
        LEFT JOIN ExecutedInstructions
        ON ExecutedInstructions.ExecInstrId = DispatchTime.ExecInstrId
        WHERE ExecInstrCoreId = :core_id
        AND CoreTypeId = :core_type_id
        AND InstrTypeId = :pipe
        AND DispatchTimeTick <= ExecInstrTickStart

        UNION ALL

        SELECT ExecutedInstructions.ExecInstrId AS ExecInstrId,
        ExecInstrTickStart AS Tick, {POP} AS State, ExecInstrName
        FROM ExecutedInstructions
        LEFT JOIN DispatchTime
        ON ExecutedInstructions.ExecInstrId = DispatchTime.ExecInstrId
        WHERE ExecInstrCoreId = :core_id
        AND CoreTypeId = :core_type_id
        AND InstrTypeId = :pipe
        AND DispatchTimeTick IS NOT NULL
        AND DispatchTimeTick <= ExecInstrTickStart

        UNION ALL

        SELECT ExecutedInstructions.ExecInstrId AS ExecInstrId,
        ExecInstrTickEnd AS Tick, {RETIRE} AS State, ExecInstrName
        FROM ExecutedInstructions
        LEFT JOIN DispatchTime
        ON ExecutedInstructions.ExecInstrId = DispatchTime.ExecInstrId
        WHERE ExecInstrCoreId = :core_id
        AND CoreTypeId = :core_type_id
        AND InstrTypeId = :pipe
        AND DispatchTimeTick IS NOT NULL
        AND DispatchTimeTick <= ExecInstrTickStart

        ORDER BY Tick
        """,
            {"core_id": core_id, "core_type_id": core_type_id, "pipe": pipe},
        ).fetchall()

    def save_states(self) -> None:
        database_handler.executemany(
            """
            INSERT INTO IssueQueueUtilization
            (Tick, Util, CoreId, CoreTypeId, InstrTypeId)
            VALUES
            (?, ?, ?, ?, ?)
            """,
            [(item.tick, item.util, item.core_id, item.core_type_id, item.pipe_id) for item in self.issue],
        )
        database_handler.executemany(
            """
            INSERT INTO ExecQueueUtilization
            (Tick, Util, CoreId, CoreTypeId, InstrTypeId)
            VALUES
            (?, ?, ?, ?, ?)
            """,
            [(item.tick, item.util, item.core_id, item.core_type_id, item.pipe_id) for item in self.exec],
        )

    def process_pipe_instrs(self, instrs: List[sqlite3.Row], core_id: int, core_type_id: int, pipe: int):
        issue_queue_balance = 0
        exec_queue_balance = 0
        for instr in instrs:
            if instr["ExecInstrName"] in self.skip_flag_instrs:
                continue
            if instr["State"] == PUSH:
                issue_queue_balance += 1
                self.issue.append(SubqueueUtilEvent(instr["Tick"], issue_queue_balance, core_id, core_type_id, pipe))
            elif instr["State"] == POP and instr["ExecInstrName"] not in SYNCHRO_INSTRS:
                issue_queue_balance -= 1
                self.issue.append(SubqueueUtilEvent(instr["Tick"], issue_queue_balance, core_id, core_type_id, pipe))
                exec_queue_balance += 1
                self.exec.append(SubqueueUtilEvent(instr["Tick"], exec_queue_balance, core_id, core_type_id, pipe))
            elif instr["State"] == RETIRE:
                if instr["ExecInstrName"] in SYNCHRO_INSTRS:
                    issue_queue_balance -= 1
                    self.issue.append(
                        SubqueueUtilEvent(
                            instr["Tick"],
                            issue_queue_balance,
                            core_id,
                            core_type_id,
                            pipe,
                        )
                    )
                else:
                    exec_queue_balance -= 1
                    self.exec.append(
                        SubqueueUtilEvent(
                            instr["Tick"],
                            exec_queue_balance,
                            core_id,
                            core_type_id,
                            pipe,
                        )
                    )

    def process(self) -> None:
        self.make_indices()
        for core_id in self.get_active_cores():
            for core_type_id in self.get_active_core_types():
                for pipe in SAMPLING_PIPES_TO_SUBCORES[CoreType(core_type_id)]:
                    instrs = self.get_pipe_instrs(core_id, core_type_id, pipe.value)
                    self.process_pipe_instrs(instrs, core_id, core_type_id, pipe.value)
        self.save_states()
        self.drop_indices()

    def cleanup(self) -> None:
        self.exec.clear()
        self.issue.clear()
