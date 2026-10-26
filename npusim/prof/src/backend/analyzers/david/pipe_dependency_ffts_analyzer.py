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
from typing import List, Dict, Tuple
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.analyzers.pipe_dependency_analyzer import (
    PipeDependencyAnalyzer,
    PipeDependencyCategory,
)
from npusim.prof.src.backend.analyzers.david.pipe_dependency_flags_analyzer import (
    pipe_map,
)
from npusim.prof.src.common.extend_params import ExtendParams


@dataclass
class FFTSParams:
    instr_id: int
    mode: int
    tick: int
    pipe: str
    core_id: int
    core_type_id: int


class Ascend950PipeDependencyFFTSAnalyzer(PipeDependencyAnalyzer):
    # Cross-core FFTS (Fast Flag Transport Sync) dependency tracker.
    # Tracks SET_CROSS_CORE / WAIT_FLAG_DEVI instruction pairs that
    # synchronize execution across different cores via hardware flags.
    # Each SET instruction is paired with the next WAIT on the same flag_id.
    # Also updates InstrTypeId for SET/WAIT instructions to reflect their
    # actual pipe assignment (extracted from target_pipe in ExtendParams).
    def __init__(self) -> None:
        super().__init__()
        self.instrs_by_flag: Dict[int, List[FFTSParams]] = defaultdict(list)

    @staticmethod
    def get_name() -> str:
        return "ascend950_pipe_dependency_ffts"

    @staticmethod
    def get_set_flag_instrs() -> List[sqlite3.Row]:
        return database_handler.execute("""
            SELECT ExtendParams, ExecInstrId, ExecInstrTickStart, ExecInstrCoreId, CoreTypeId
            FROM ExecutedInstructions
            WHERE ExecInstrName LIKE '%SET_CROSS_CORE%'
            ORDER BY ExecInstrTickStart
            """).fetchall()

    @staticmethod
    def get_wait_flag_instrs() -> List[sqlite3.Row]:
        return database_handler.execute("""
            SELECT ExtendParams, ExecInstrId, ExecInstrCoreId, CoreTypeId
            FROM ExecutedInstructions
            WHERE ExecInstrName LIKE '%WAIT_FLAG_DEV%'
            ORDER BY ExecInstrTickStart
            """).fetchall()

    @staticmethod
    def get_flag_index(flag_id: int, pipe: int) -> int:
        ret = pipe << 4
        ret |= flag_id
        return ret

    @staticmethod
    def change_instr_type_id(instrs: List[Tuple[int, int]]) -> None:
        database_handler.executemany(
            """
            UPDATE ExecutedInstructions
            SET InstrTypeId=?
            WHERE ExecInstrId=?
            """,
            instrs,
        )

    def _find_instr_index(self, flag_id: int, instr: sqlite3.Row) -> int:
        exec_core_id = int(instr["ExecInstrCoreId"])
        core_type_id = int(instr["CoreTypeId"])
        for i, item in enumerate(self.instrs_by_flag[flag_id]):
            if item.core_id == exec_core_id and item.core_type_id == core_type_id:
                return i
        return -1

    def process_mode_0(self, flag_id: int, pipe: str, instr: sqlite3.Row) -> None:
        idx = self._find_instr_index(flag_id, instr)
        if idx == -1:
            return
        set_instr = self.instrs_by_flag[flag_id][idx]
        flag_index = self.get_flag_index(flag_id, pipe_map[set_instr.pipe])
        flow_event_id = self.get_flow_event_id(flag_index, set_instr.instr_id)
        flow_event_name = self.get_flow_event_name(set_instr.pipe, pipe, flag_id)
        self.store_pipe_dependency(
            set_instr.instr_id,
            flow_event_id,
            flow_event_name,
            False,
            PipeDependencyCategory.FFTS.value,
        )
        self.store_pipe_dependency(
            instr["ExecInstrId"],
            flow_event_id,
            flow_event_name,
            True,
            PipeDependencyCategory.FFTS.value,
        )
        self.instrs_by_flag[flag_id].pop(idx)

    @staticmethod
    def get_flow_event_name(set_pipe: str, wait_pipe: str, flag_id: int) -> str:
        return f"{wait_pipe} awaiting {set_pipe} flag id #{flag_id}"

    def process(self) -> None:
        set_instrs = self.get_set_flag_instrs()
        update_instr_type: List[Tuple[int, int]] = []
        for instr in set_instrs:
            ep = ExtendParams.parse(instr["ExtendParams"])
            flag_id = ep.ffts_flag_id
            pipe = ep.target_pipe
            assert flag_id is not None and pipe is not None, (
                f"Cannot extract FFTS flag_id/pipe from {dict(instr)} instruction."
            )
            update_instr_type.append(
                (
                    InstrType.PUSHQ.value if pipe == "VEC" else InstrType[pipe].value,
                    instr["ExecInstrId"],
                )
            )
            mode = ep.ffts_mode or 0
            params = FFTSParams(
                int(instr["ExecInstrId"]),
                mode,
                int(instr["ExecInstrTickStart"]),
                pipe,
                int(instr["ExecInstrCoreId"]),
                int(instr["CoreTypeId"]),
            )
            self.instrs_by_flag[flag_id].append(params)
        wait_instrs = self.get_wait_flag_instrs()
        for instr in wait_instrs:
            ep = ExtendParams.parse(instr["ExtendParams"])
            flag_id = ep.ffts_flag_id
            pipe = ep.target_pipe
            assert flag_id is not None and pipe is not None, (
                f"Cannot extract FFTS flag_id/pipe from {dict(instr)} instruction."
            )
            update_instr_type.append(
                (
                    InstrType.PUSHQ.value if pipe == "VEC" else InstrType[pipe].value,
                    instr["ExecInstrId"],
                )
            )
            if not self.instrs_by_flag[flag_id]:
                continue
            params = self.instrs_by_flag[flag_id][0]

            if params.mode == 0:
                self.process_mode_0(flag_id, pipe, instr)
        self.change_instr_type_id(update_instr_type)
