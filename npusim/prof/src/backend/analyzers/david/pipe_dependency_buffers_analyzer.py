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
from typing import List, Dict, Optional
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.analyzers.pipe_dependency_analyzer import (
    PipeDependencyAnalyzer,
    PipeDependencyCategory,
)
from npusim.prof.src.common.extend_params import ExtendParams


@dataclass
class BufParam:
    pipe: str
    buf_id: int
    mode: int
    cnt: int


@dataclass(frozen=True)
class BufIdx:
    buf_id: int
    cnt: int
    core_id: int
    core_type_id: int


class Ascend950PipeDependencyBuffersAnalyzer(PipeDependencyAnalyzer):
    def __init__(self) -> None:
        super().__init__()

    @staticmethod
    def get_name() -> str:
        return "ascend950_pipe_dependency_buffers"

    @staticmethod
    def get_buf_instrs() -> List[sqlite3.Row]:
        executed_instrs = database_handler.execute(
            """
            SELECT ExtendParams, ExecInstrId, ExecInstrName, ExecInstrCoreId, CoreTypeId
            FROM ExecutedInstructions
            WHERE ExecInstrName LIKE "GET_BUF%" OR
                  ExecInstrName LIKE "RLS_BUF%"
            ORDER BY ExecInstrTickStart
            """
        )
        result: List[sqlite3.Row] = executed_instrs.fetchall()
        return result

    @staticmethod
    def get_buf_params(instr: sqlite3.Row) -> Optional[BufParam]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        pipe = ep.target_pipe
        buf_id = ep.buf_id
        buf_mode = ep.buf_mode
        buf_cnt = ep.buf_cnt
        if pipe is None or buf_id is None or buf_mode is None or buf_cnt is None:
            return None
        return BufParam(pipe, buf_id, buf_mode, buf_cnt)

    def get_buf_index(self, buf_param: BufParam, core_id: int, core_type_id: int) -> BufIdx:
        return BufIdx(buf_param.buf_id, buf_param.cnt, core_id, core_type_id)

    @staticmethod
    def get_flow_event_index(idx: BufIdx) -> int:
        index = idx.buf_id << 16
        index |= idx.cnt << 7
        index |= idx.core_id << 2
        index |= idx.core_type_id
        return index

    def get_prev_index(self, param: BufParam, core_id: int, core_type_id: int) -> BufIdx:
        new_buf_param = BufParam(param.pipe, param.buf_id, param.mode, param.cnt - 1)
        return self.get_buf_index(new_buf_param, core_id, core_type_id)

    @staticmethod
    def get_flow_event_name(params: BufParam) -> str:
        res = f"{params.pipe} pipe awaiting RLS_BUF ID: {params.buf_id}, " + f"count: {params.cnt}, pipe is blocked."
        return res

    @staticmethod
    def change_get_rls_buf_instr_type_id(instr_id: int, pipe: str) -> None:
        instr_type_id = InstrType.PUSHQ.value if pipe == "VEC" else InstrType[pipe].value
        database_handler.execute(
            """
            UPDATE ExecutedInstructions
            SET InstrTypeId = :instr_type_id
            WHERE ExecInstrId = :exec_instr_id
            """,
            {"instr_type_id": instr_type_id, "exec_instr_id": instr_id},
        )

    def process(self) -> None:
        buf_instrs = self.get_buf_instrs()
        buffers: Dict[BufIdx, int] = defaultdict(int)
        for instr in buf_instrs:
            params = self.get_buf_params(instr)
            if params is None:
                continue
            instr_id = int(instr["ExecInstrId"])
            self.change_get_rls_buf_instr_type_id(instr_id, params.pipe)
            if params.mode == 1 and "GET_BUF" in instr["ExecInstrName"]:
                continue
            if "GET_BUF" in instr["ExecInstrName"] and params.cnt < 1:
                continue

            index = self.get_buf_index(params, int(instr["ExecInstrCoreId"]), int(instr["CoreTypeId"]))
            if "GET_BUF" in instr["ExecInstrName"]:
                index = self.get_prev_index(params, int(instr["ExecInstrCoreId"]), int(instr["CoreTypeId"]))
                if buffers[index] == 0:
                    continue
                params = BufParam(params.pipe, params.buf_id, params.mode, params.cnt - 1)

            if buffers[index] == 0:
                # flow begin
                buffers[index] = instr_id
            else:
                # flow end
                flow_event_id = self.get_flow_event_id(self.get_flow_event_index(index), buffers[index])
                flow_event_name = self.get_flow_event_name(params)
                self.store_pipe_dependency(
                    buffers[index],
                    flow_event_id,
                    flow_event_name,
                    False,
                    PipeDependencyCategory.BUFFERS.value,
                )
                self.store_pipe_dependency(
                    instr_id,
                    flow_event_id,
                    flow_event_name,
                    True,
                    PipeDependencyCategory.BUFFERS.value,
                )
                buffers[index] = 0
