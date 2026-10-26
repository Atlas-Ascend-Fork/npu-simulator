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

from enum import Enum, auto
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler


class PipeDependencyCategory(Enum):
    FLAGS = auto()
    INTRA_BLOCKS = auto()
    BUFFERS = auto()
    FFTS = auto()


class PipeDependencyAnalyzer(IAnalyzer):
    INSTR_ID_PAYLOAD_MASK = (1 << 40) - 1

    def __init__(self) -> None:
        super().__init__()

        max_instr = database_handler.execute(f"""
        SELECT COALESCE(
            MAX(ExecInstrId & {self.INSTR_ID_PAYLOAD_MASK}), 0
        ) as MaxID
        FROM ExecutedInstructions
        """).fetchone()

        self.MaxInstrID = int(max_instr["MaxID"]) + 1

    def get_flow_event_id(self, index: int, instr_id: int) -> int:
        # Use only bits 39..0; upper bits contain InstrTypeId and break logic
        # leading to SQLite INTEGER overflow error
        return (index + 1) * self.MaxInstrID + (instr_id & self.INSTR_ID_PAYLOAD_MASK)

    @staticmethod
    def store_pipe_dependency(
        instr_id: int,
        flow_event_id: int,
        flow_event_name: str,
        flow_end: bool,
        cat_id: int,
    ) -> None:
        database_handler.execute(
            f"""
            INSERT INTO PipeDependency
            VALUES ({",".join("?" * 5)})
            """,
            (instr_id, flow_event_id, flow_event_name, flow_end, cat_id),
        )
