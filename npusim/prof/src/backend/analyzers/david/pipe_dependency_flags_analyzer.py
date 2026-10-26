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

from collections import defaultdict, deque
import sqlite3
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.pipe_dependency_analyzer import (
    PipeDependencyAnalyzer,
    PipeDependencyCategory,
)
from npusim.prof.src.common.extend_params import ExtendParams


pipe_map = {
    "SCALAR": 0,
    "VEC": 1,
    "CUBE": 2,
    "MTE1": 3,
    "MTE2": 4,
    "MTE3": 5,
    "FC": 6,
    "FIXP": 7,
}


@dataclass
class FlagParams:
    exec_pipe: str
    consumer_pipe: str
    event_id: int


class FlowState:
    SET = 0b01
    WAIT = 0b10
    COMPLETE = SET | WAIT

    def __init__(self, instr_name: str, instr_id: int) -> None:
        self.instr_id = instr_id
        self.state = self.SET if instr_name == "SET_FLAG" else self.WAIT

    def update(self, instr_name: str) -> None:
        self.state |= self.SET if instr_name == "SET_FLAG" else self.WAIT

    def is_complete(self) -> bool:
        return self.state == self.COMPLETE

    def __repr__(self) -> str:
        return f"FlowState(state={self.state}, instr_id={self.instr_id})"

    def __str__(self) -> str:
        return self.__repr__()


class Ascend950PipeDependencyFlagsAnalyzer(PipeDependencyAnalyzer):
    supported_instrs = (
        "SET_FLAG",
        "WAIT_FLAG",
    )

    def process(self) -> None:
        flows: Dict[int, Deque[FlowState]] = defaultdict(deque)

        flag_instrs = self.get_flag_instrs()
        for instr in flag_instrs:
            flag_params = self.get_flag_params(instr)
            if flag_params is None:
                continue

            instr_id = int(instr["ExecInstrId"])
            instr_name = str(instr["ExecInstrName"])

            flag_index = self.get_flag_index(flag_params, instr)

            flow_event_id = None
            is_flow_start = True

            if len(flows[flag_index]) == 0:
                # Flow start
                # Append new state to the start (left) of the queue
                flows[flag_index].appendleft(FlowState(instr_name, instr_id))
                flow_event_id = self.get_flow_event_id(flag_index, instr_id)
            else:
                # Watch the end (right) element of the queue
                flow = flows[flag_index][-1]
                flow.update(instr_name)
                # Check that the flow consists of a set/wait pair
                if flow.is_complete():
                    # Flow end
                    flow_event_id = self.get_flow_event_id(flag_index, flow.instr_id)
                    # Pop the end (right) element from the queue
                    flows[flag_index].pop()
                    is_flow_start = False
                else:
                    # Flow start
                    # Append new state to the start (left) of the queue
                    flows[flag_index].appendleft(FlowState(instr_name, instr_id))
                    flow_event_id = self.get_flow_event_id(flag_index, instr_id)

            # Flow store
            flow_event_name = self.get_flow_event_name(
                flag_params.exec_pipe,
                flag_params.consumer_pipe,
                flag_params.event_id,
            )
            self.store_pipe_dependency(
                instr_id,
                flow_event_id,
                flow_event_name,
                not is_flow_start,
                PipeDependencyCategory.FLAGS.value,
            )

    @classmethod
    def get_flag_instrs(cls) -> List[sqlite3.Row]:
        instr_list = str(cls.supported_instrs).replace(",)", ")")
        executed_instrs = database_handler.execute(
            """
        SELECT ExtendParams, ExecInstrId, ExecInstrName, ExecInstrCoreId, CoreTypeId
        FROM ExecutedInstructions
        WHERE ExecInstrName IN {}
        ORDER BY ExecInstrTickStart, ExecInstrId
        """.format(instr_list)
        )
        return executed_instrs.fetchall()

    @staticmethod
    def get_flag_params(instr: sqlite3.Row) -> Optional[FlagParams]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        exec_pipe = ep.target_pipe
        consumer_pipe = ep.consumer_pipe
        flag_id = ep.flag_id
        if exec_pipe is None or consumer_pipe is None or flag_id is None:
            return None
        return FlagParams(exec_pipe, consumer_pipe, flag_id)

    def get_flag_index(self, flag_params: FlagParams, instr: sqlite3.Row) -> int:
        event_id = flag_params.event_id
        exec_pipe_id = pipe_map[flag_params.exec_pipe]
        consumer_pipe_id = pipe_map[flag_params.consumer_pipe]
        core_id = int(instr["ExecInstrCoreId"])
        core_type_id = int(instr["CoreTypeId"])

        flag_index = exec_pipe_id << 13
        flag_index |= consumer_pipe_id << 10
        flag_index |= event_id << 7
        flag_index |= core_id << 2
        flag_index |= core_type_id

        return flag_index

    @staticmethod
    def get_name() -> str:
        return "ascend950_pipe_dependency_flags"

    @staticmethod
    def get_flow_event_name(exec_pipe: str, consumer_pipe: str, event_id: int) -> str:
        return f"{consumer_pipe} awaiting {exec_pipe} event #{event_id}"
