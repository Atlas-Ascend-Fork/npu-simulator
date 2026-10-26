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
import logging as log
import json
from typing import List, Dict, Tuple
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import V100_SIMD_STALL_REASONS
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


class Ascend950SIMDSamplingAnalyzer(IAnalyzer):
    @staticmethod
    def get_name() -> str:
        return "ascend950_simd_sampling_analyzer"

    @staticmethod
    def get_instrs() -> List[sqlite3.Row]:
        return database_handler.execute("""
        SELECT ExecutedInstructions.SourceInstrAddr, StallReasonName,
               StallCount, SIMDSamplingStats.ExecInstrId
        FROM SIMDSamplingStats
        LEFT JOIN SIMDStallReasonTypes
        ON SIMDStallReasonTypes.StallReasonId =
        SIMDSamplingStats.StallReasonId
        LEFT JOIN ExecutedInstructions
        ON ExecutedInstructions.ExecInstrId =
        SIMDSamplingStats.ExecInstrId
        """).fetchall()

    @staticmethod
    def get_source_lines() -> List[sqlite3.Row]:
        return database_handler.execute("""
        SELECT SourceInstrAddr,
        DebugInfo.SourceFileId,
        DebugInfo.DebugInfoLineNum
        FROM SourceInstructions
        JOIN DebugInfo
        ON DebugInfo.DebugInfoId = SourceInstructions.DebugInfoId
        """).fetchall()

    @staticmethod
    def save_instr_stalls(instrs: Dict[int, Dict[str, int]]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SIMDStallsByAddr (
            SourceInstrAddr, StallsJson
        )
        VALUES
        (?, ?)
        """,
            [(key, json.dumps(val)) for key, val in instrs.items()],
        )

    @staticmethod
    def save_lines_stalls(lines: Dict[Tuple[int, int], Dict[str, int]]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SIMDStallsByLine (
            SourceFileId, DebugInfoLineNum, StallsJson
        )
        VALUES
        (?, ?, ?)
        """,
            [(key[0], key[1], json.dumps(val)) for key, val in lines.items()],
        )

    @staticmethod
    def _process(instrs: List[sqlite3.Row]) -> Dict[int, Dict[str, int]]:
        aggregated_instrs: Dict[int, Dict[str, int]] = {}
        for instr in instrs:
            if not instr["SourceInstrAddr"]:
                # log.debug(
                #     f"Couldn't find 'SourceInstrAddr' "
                #     f"for instruction id {instr['ExecInstrId']}")
                continue
            if instr["SourceInstrAddr"] not in aggregated_instrs:
                aggregated_instrs[instr["SourceInstrAddr"]] = defaultdict(int)
            if instr["StallReasonName"] not in V100_SIMD_STALL_REASONS:
                log.warning(f"Cannot find '{instr['StallReasonName']}' in default set of SIMD stall reasons")
                continue
            if instr["StallReasonName"] not in aggregated_instrs[instr["SourceInstrAddr"]]:
                aggregated_instrs[instr["SourceInstrAddr"]][instr["StallReasonName"]] = instr["StallCount"]
            else:
                aggregated_instrs[instr["SourceInstrAddr"]][instr["StallReasonName"]] += instr["StallCount"]
        return aggregated_instrs

    @classmethod
    def aggregate_by_lines(cls, instrs: Dict[int, Dict[str, int]]) -> Dict[Tuple[int, int], Dict[str, int]]:
        aggregated_lines: Dict[Tuple[int, int], Dict[str, int]] = defaultdict(dict)
        source_lines = cls.get_source_lines()
        for item in source_lines:
            if item["SourceInstrAddr"] not in instrs:
                continue
            acc = aggregated_lines[(item["SourceFileId"], item["DebugInfoLineNum"])]
            for reason, count in instrs[item["SourceInstrAddr"]].items():
                acc[reason] = acc.get(reason, 0) + count

        return aggregated_lines

    def process(self) -> None:
        instrs = self.get_instrs()
        agg_instrs = self._process(instrs)
        agg_lines = self.aggregate_by_lines(agg_instrs)
        self.save_instr_stalls(agg_instrs)
        self.save_lines_stalls(agg_lines)
