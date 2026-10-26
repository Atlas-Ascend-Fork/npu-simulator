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

import re
from typing import List, Iterator
from sqlite3 import Row
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams


class DatabaseManager:
    @staticmethod
    def iter_instructions(cid: int) -> Iterator[Row]:
        instr_types = [
            InstrType.PUSHQ.value,
            InstrType.CUBE.value,
            InstrType.MTE1.value,
            InstrType.MTE2.value,
            InstrType.MTE3.value,
            InstrType.FIXP.value,
            InstrType.FC.value,
            InstrType.SCALAR.value,
        ]
        instr_str = str(tuple(instr_types)).replace(",)", ")")
        return database_handler.iter_rows(
            """
        SELECT EI.ExecInstrId as InstrId, ExtendParams,
               InstrTypeId, ExecInstrTickStart,
               EventId, FlowEnd, CoreTypeId
        FROM ExecutedInstructions AS EI
        LEFT JOIN PipeDependency AS PD
        ON EI.ExecInstrId == PD.ExecInstrId
        WHERE InstrTypeId IN {}
        AND ExecInstrCoreId == :core_id
        ORDER BY ExecInstrTickEnd DESC, FlowEnd DESC
        """.format(instr_str),
            {"core_id": cid},
        )

    @staticmethod
    def save_cpath(
        data: List[int],
    ) -> List[Row]:
        neo_data: List[List[int]] = list(map(lambda x: [x], data))
        cur = database_handler.executemany(
            """
        INSERT INTO CriticalPath(InstrId)
        VALUES (?)
        """,
            neo_data,
        )
        return cur.fetchall()


class CriticalPathAnalyzer(IAnalyzer):
    # Identifies the critical execution path through a kernel by tracking
    # pipe dependencies (flow events in CT). The critical path determines the
    # minimum execution time — any instruction on it delays the whole kernel.
    #
    # Algorithm: scan instructions in reverse tick order. Track which pipe
    # is the current bottleneck (focus_pipe). When a sync/flow event ends,
    # switch focus to the dependent pipe. The collected instruction IDs form
    # the critical path (reversed at the end to restore forward order).
    pat_intra_block = re.compile(r"PIPE:(?P<pipe>[A-Za-z0-9]+),")
    pat_normal = re.compile(
        r"PIPE:(?P<pipe>[A-Za-z0-9]+),.+"
        r"TRIGGER PIPE:(?P<t_pipe>[A-Za-z0-9]+),"
    )
    pipe_dict = {
        "VEC": InstrType.PUSHQ.value,
        "CUBE": InstrType.CUBE.value,
        "FIXP": InstrType.FIXP.value,
        "MTE1": InstrType.MTE1.value,
        "MTE2": InstrType.MTE2.value,
        "MTE3": InstrType.MTE3.value,
        "SCALAR": InstrType.SCALAR.value,
        "FC": InstrType.FC.value,
    }

    @staticmethod
    def get_name() -> str:
        return "ascend950_critical_path"

    def extract_pipe(self, instr: Row, flow_end: bool) -> int:
        ep = ExtendParams.parse(instr["ExtendParams"])
        pipe = ep.target_pipe
        if flow_end and ep.consumer_pipe is not None:
            pipe = ep.consumer_pipe
        if pipe is None or pipe not in self.pipe_dict:
            raise Exception(f"pipe extraction failed for {dict(instr)}")
        return self.pipe_dict[pipe]

    def make_cpath_graph(self, instrs_iter: Iterator[Row]) -> List[int]:
        critical_path = []
        focus_pipe = None
        last_instr_start = {}
        # EventId -> (InstrId, Pipe, InstrTickStart)
        flow_end_events = {}
        for instr in instrs_iter:
            instr_id = instr["InstrId"]
            pipe = instr["InstrTypeId"]
            core = instr["CoreTypeId"]
            tick_start = instr["ExecInstrTickStart"]
            event_id = instr["EventId"]
            if event_id is None:
                # normal instruction
                if pipe is not InstrType.SCALAR.value and pipe is not InstrType.FC.value:
                    last_instr_start[(pipe, core)] = tick_start
                    if focus_pipe is None:
                        focus_pipe = (pipe, core)
                if (pipe, core) == focus_pipe:
                    critical_path.append(instr_id)
            else:
                pipe = self.extract_pipe(instr, instr["FlowEnd"])
                # sync instruction
                if instr["FlowEnd"]:
                    if (pipe, core) in last_instr_start and last_instr_start[(pipe, core)] < tick_start:
                        continue
                    flow_end_events[event_id] = (instr_id, pipe, core)
                elif event_id in flow_end_events:
                    e_id, e_pipe, e_core = flow_end_events[event_id]
                    if focus_pipe is None or (e_pipe, e_core) == focus_pipe:
                        critical_path.extend([instr_id, e_id])
                        focus_pipe = (pipe, core)
                        flow_end_events.clear()
        critical_path.reverse()
        return critical_path

    def process(self) -> None:
        for core_id in self.get_active_cores():
            instrs_iter = DatabaseManager.iter_instructions(core_id)
            cpath = self.make_cpath_graph(instrs_iter)
            DatabaseManager.save_cpath(cpath)
