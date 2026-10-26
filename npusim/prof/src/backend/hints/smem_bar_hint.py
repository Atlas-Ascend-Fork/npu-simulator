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
from typing import Any, Dict, List, Set, Tuple

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.common.biprof_constants import RELTIME_THRESHOLD


class SmemBarHint(IHint):
    # Ported from biprof: the original detected an auto-inserted shared
    # memory barrier (RV_SMEM_BAR instruction) between an RV_VST and an
    # RV_VLD of the same UB address. The contract DB has no barrier
    # instruction, so the same pattern is detected from per-instruction
    # UB addresses (UbRwAccesses via the SharedPatterns view): an RV_VST
    # immediately followed by an RV_VLD (in per-core execution order)
    # that touches an address written by that RV_VST.

    def __init__(self) -> None:
        super().__init__()
        self.hint_pcs: Set[int] = set()

        rows = self.db_retrieve_instrs()
        self.find_hinted_pcs(rows)

    @staticmethod
    def get_name() -> str:
        return "v310_smem_bar_hint"

    def db_retrieve_instrs(self) -> List[sqlite3.Row]:
        query = database_handler.execute(
            """
        SELECT ExecutedInstructions.ExecInstrCoreId AS CoreId,
               ExecutedInstructions.ExecInstrId AS Id,
               ExecutedInstructions.ExecInstrName AS Name,
               ExecutedInstructions.SourceInstrAddr AS PC,
               SharedPatterns.AccessedAddress AS Addr
        FROM SharedPatterns
        INNER JOIN ExecutedInstructions
        ON        SharedPatterns.ExecInstrId = ExecutedInstructions.ExecInstrId
        WHERE SharedPatterns.InstrName IN ('RV_VLD', 'RV_VST')
        ORDER BY ExecutedInstructions.ExecInstrCoreId,
                 ExecutedInstructions.ExecInstrId
        """
        )
        return query.fetchall()

    def find_hinted_pcs(self, rows: List[sqlite3.Row]) -> None:
        # Rows hold one entry per accessed address; group them back into
        # per-executed-instruction address sets. The query is ordered by
        # (core, instruction id), so the dict preserves execution order.
        instrs: Dict[Tuple[int, int], List[Any]] = {}
        for row in rows:
            key = (row["CoreId"], row["Id"])
            if key not in instrs:
                instrs[key] = [row["CoreId"], row["Name"], row["PC"], set()]
            instrs[key][3].add(row["Addr"])
        ordered = list(instrs.values())
        for prev_instr, curr_instr in zip(ordered, ordered[1:]):
            prev_core, prev_name, prev_pc, prev_addrs = prev_instr
            curr_core, curr_name, curr_pc, curr_addrs = curr_instr
            if prev_core == curr_core and prev_name == "RV_VST" and curr_name == "RV_VLD" and prev_addrs & curr_addrs:
                self.hint_pcs.add(prev_pc)
                self.hint_pcs.add(curr_pc)

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return instr["PC"] in self.hint_pcs and float(instr["RelTime"]) >= RELTIME_THRESHOLD

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return (
            "Inefficiency caused by automatically placed "
            "memory barriers detected. Consider rewriting "
            "the section in a vector scope to improve performance."
        )
