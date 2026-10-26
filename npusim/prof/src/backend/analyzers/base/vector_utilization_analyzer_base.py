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

from dataclasses import dataclass
import sqlite3
from typing import List, Optional, Iterator
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


@dataclass
class VecUtilEntry:
    exec_instr_id: int
    processed_bytes: int
    processed_elements: Optional[int]
    vec_util_value: float


class VectorUtilizationAnalyzerBase(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.utilizations: List[VecUtilEntry] = []

    @staticmethod
    def get_name() -> str:
        return "vector_utilization"

    @staticmethod
    def iter_vec_instrs() -> Iterator[sqlite3.Row]:
        raise NotImplementedError

    @staticmethod
    def is_supported_instr(instr: sqlite3.Row) -> bool:
        raise NotImplementedError

    def calc_utilization(self, instr: sqlite3.Row) -> Optional[VecUtilEntry]:
        raise NotImplementedError

    def save_utilizations(self) -> None:
        database_handler.executemany(
            """
            INSERT INTO VectorUtilizations
            (ExecInstrId,
             ProcessedBytes,
             ProcessedElements,
             VectorUtilization)
            VALUES
            (?, ?, ?, ?)
        """,
            [
                (
                    entry.exec_instr_id,
                    entry.processed_bytes,
                    entry.processed_elements,
                    entry.vec_util_value,
                )
                for entry in self.utilizations
            ],
        )

    def process(self) -> None:
        for instr in self.iter_vec_instrs():
            if not self.is_supported_instr(instr):
                continue
            vec_util_entry = self.calc_utilization(instr)
            if vec_util_entry:
                self.utilizations.append(vec_util_entry)
        self.save_utilizations()

    def cleanup(self) -> None:
        self.utilizations.clear()
