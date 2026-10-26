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

from collections import defaultdict
import sqlite3
from typing import Dict, List, Optional, Set, Tuple, Iterator
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.exceptions import RequirementsIncomplete


class DatabaseManager:
    @staticmethod
    def iter_instrs() -> Iterator[sqlite3.Row]:
        return database_handler.iter_rows("""
            SELECT
                SourceInstrAddr AS PC,
                ExecInstrCoreId AS CoreId,
                CoreTypeId      AS CoreTypeId,
                ExecInstrName   AS Name,
                (ExecInstrTickEnd - ExecInstrTickStart) AS TickCount
            FROM ExecutedInstructions
        """)

    @staticmethod
    def save_usage_data(update: List[Tuple[int, int, Optional[int]]]) -> None:
        database_handler.executemany(
            """
            INSERT INTO UnitsUsageMetrics
            (SourceInstrAddr, UniqueSubCoresUsed, UniqueWarpsUsed)
            VALUES
            (?, ?, ?)
            """,
            update,
        )


class InstrUnitsUsageAnalyzer(IAnalyzer):
    @staticmethod
    def get_name() -> str:
        return "instr_units_usage"

    def check_requirements(self) -> None:
        if not database_handler.count_rows("ExecutedInstructions"):
            raise RequirementsIncomplete("No executed instructions found")

    @classmethod
    def collect_usage_data(cls, instrs: Iterator[sqlite3.Row]) -> List[Tuple[int, int, Optional[int]]]:
        collected_data: List[Tuple[int, int, Optional[int]]] = []

        # mapping from source addr (PC)
        # to a set of unique (core_id, core_type_id) pairs (subcores)
        addr_to_subcores_set: Dict[int, Set[Tuple[int, int]]] = defaultdict(set)
        # mapping from source addr (PC)
        # to a set of unique (core_id, core_type_id, warp_id) pairs (warps)
        addr_to_warps_set: Dict[int, Set[Tuple[int, int, int]]] = defaultdict(set)

        for instr in instrs:
            addr_to_subcores_set[instr["PC"]].add((instr["CoreId"], instr["CoreTypeId"]))
            if instr["Name"].startswith("SIMT_"):
                warps_used = None

        for pc in addr_to_subcores_set.keys():
            subcores_used = len(addr_to_subcores_set[pc])
            warps_used: Optional[int] = len(addr_to_warps_set[pc])
            if not warps_used:
                warps_used = None
            collected_data.append((pc, subcores_used, warps_used))
        return collected_data

    def process(self) -> None:
        instrs = DatabaseManager.iter_instrs()
        data = self.collect_usage_data(instrs)
        DatabaseManager.save_usage_data(data)
