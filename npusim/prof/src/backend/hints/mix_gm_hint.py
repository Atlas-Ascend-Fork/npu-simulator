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
from typing import Set, List, Dict, Tuple
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.extend_params import ExtendParams


class MixGMHint(IHint):
    # Detects inefficient data exchange between Cube and Vector cores
    # via global memory (OUT).  A cube→OUT write followed by a vec←OUT
    # read (or vice versa) at the same address indicates the data could
    # have been exchanged directly without going through global memory.
    #
    # With the contract DB, src_addr/dst_addr in ExtendParams are used
    # to pair writes and reads at the same OUT address.  Entries where
    # the needed address is None are skipped.
    def __init__(self) -> None:
        super().__init__()
        self.hint_pcs: Set[int] = set()

        rows = self.db_retrieve_instrs()
        self.find_hinted_pcs(rows)

    def db_retrieve_instrs(self) -> List[sqlite3.Row]:
        gm_instr_list = [
            "MOV_SRC_TO_DST_ALIGNv2",
            "MOV_OUT_TO_L1_MULTI_ND2NZ",
            "MOV_OUT_TO_L1_MULTI_DN2NZ",
            "FIX_L0C_TO_DST",
        ]
        list_str = str(tuple(gm_instr_list)).replace(",)", ")")

        query = database_handler.execute(
            """
        SELECT ExtendParams AS Params,
               ExecInstrCoreId AS Id,
               SourceInstrAddr as PC
        FROM ExecutedInstructions
        WHERE ExecInstrName IN {}
        """.format(list_str)
        )
        return query.fetchall()

    @staticmethod
    def _parse(row: sqlite3.Row) -> Tuple[str, str, int, int, int]:
        """Return (src_mem, dst_mem, core_id, src_addr, dst_addr).

        Addresses are 0 when absent (None normalised to 0 for hashing).
        """
        ep = ExtendParams.parse(row["Params"])
        src_mem = ep.src_mem or ""
        dst_mem = ep.dst_mem or ""
        core_id = int(row["Id"])
        src_addr = ep.src_addr if ep.src_addr is not None else 0
        dst_addr = ep.dst_addr if ep.dst_addr is not None else 0
        return src_mem, dst_mem, core_id, src_addr, dst_addr

    def find_hinted_pcs(self, rows: List[sqlite3.Row]):
        # cube_outs:  (core_id, dst_addr) -> PC   for cube→OUT writes
        # vec_outs:   (core_id, dst_addr) -> PC   for vec→OUT writes
        cube_outs: Dict[Tuple[int, int], int] = {}
        vec_outs: Dict[Tuple[int, int], int] = {}

        for row in rows:
            src_mem, dst_mem, core_id, _src_addr, dst_addr = self._parse(row)
            if dst_mem != "OUT" or dst_addr == 0:
                continue
            key = (core_id, dst_addr)
            if src_mem in ("L1", "L0C", "L0C32"):
                cube_outs[key] = row["PC"]
            if src_mem == "UB":
                vec_outs[key] = row["PC"]

        for row in rows:
            src_mem, dst_mem, core_id, src_addr, _dst_addr = self._parse(row)
            if src_mem != "OUT" or src_addr == 0:
                continue
            key = (core_id, src_addr)
            if dst_mem == "L1" and key in vec_outs:
                self.hint_pcs.add(row["PC"])
                self.hint_pcs.add(vec_outs[key])
            if dst_mem == "UB" and key in cube_outs:
                self.hint_pcs.add(row["PC"])
                self.hint_pcs.add(cube_outs[key])

    @staticmethod
    def get_name() -> str:
        return "mix_gm_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return instr["PC"] in self.hint_pcs

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return (
            "Inefficient data exchange between Cube and Vector detected "
            "(via global memory). Consider exchanging data directly, not "
            "using global memory"
        )
