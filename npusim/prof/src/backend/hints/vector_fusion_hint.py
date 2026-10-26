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
from typing import Set, List
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.backend.database import database_handler


class VectorFusionHint(IHint):
    # Ported from biprof: detects VF blocks that could not be fused
    # because the data they load was stored by a previous VF block (RAW
    # through UB). Load/store addresses are taken from per-instruction
    # UB accesses (UbRwAccesses via the SharedPatterns view), ordered by
    # (core, instruction id); VF/VFI block boundaries come from the
    # executed instruction ticks.

    def __init__(self) -> None:
        super().__init__()
        self.hint_pcs: Set[int] = set()

        vf_list = self.db_retrieve_vf()
        ld_st_list = self.db_retrieve_ld_st()
        self.find_hinted_pcs(vf_list, ld_st_list)

    def db_retrieve_ld_st(self) -> List[sqlite3.Row]:
        query = database_handler.execute(
            """
        SELECT ExecutedInstructions.ExecInstrTickEnd as TickEnd,
               ExecutedInstructions.ExecInstrName as Name,
               SharedPatterns.AccessedAddress as Addr
        FROM SharedPatterns
        INNER JOIN ExecutedInstructions
        ON        SharedPatterns.ExecInstrId = ExecutedInstructions.ExecInstrId
        WHERE SharedPatterns.InstrName IN ('RV_VLD', 'RV_VST')
        ORDER BY ExecutedInstructions.ExecInstrCoreId,
                 ExecutedInstructions.ExecInstrId
        """
        )
        return query.fetchall()

    def db_retrieve_vf(self) -> List[sqlite3.Row]:
        query = database_handler.execute("""
        SELECT SourceInstrAddr as PC,
               ExecInstrTickEnd as TickEnd
        FROM ExecutedInstructions
        WHERE ExecInstrName == 'VF' OR ExecInstrName == 'VFI'
        ORDER BY ExecInstrCoreId, ExecInstrId
        """)
        return query.fetchall()

    def compare_sets(self, set1: Set[int], set2: Set[int]) -> bool:
        if len(set1) == 0 or len(set2) == 0:
            return False
        breakpoint = min(len(set1), len(set2)) * 0.7
        return len(set1.intersection(set2)) > breakpoint

    def find_hinted_pcs(self, vf_list: List[sqlite3.Row], ld_st_list: List[sqlite3.Row]) -> None:
        if not vf_list:
            return
        vf_index: int = 0
        vf_past_pc: int = -1
        st_past_set: Set[int] = set()
        st_set: Set[int] = set()
        ld_set: Set[int] = set()

        for instr in ld_st_list:
            while vf_index < len(vf_list) and instr["TickEnd"] > vf_list[vf_index]["TickEnd"]:
                if self.compare_sets(st_past_set, ld_set) and vf_past_pc != vf_list[vf_index]["PC"]:
                    self.hint_pcs.add(vf_list[vf_index]["PC"])
                st_past_set = st_set
                st_set = set()
                ld_set = set()
                vf_past_pc = vf_list[vf_index]["PC"]
                vf_index += 1
            addr = instr["Addr"]
            if addr is None:
                continue
            if instr["Name"] == "RV_VLD":
                ld_set.add(addr)
            else:
                st_set.add(addr)
        if vf_index < len(vf_list) and (
            self.compare_sets(st_past_set, ld_set) and vf_past_pc != vf_list[vf_index]["PC"]
        ):
            self.hint_pcs.add(vf_list[vf_index]["PC"])

    @staticmethod
    def get_name() -> str:
        return "vector_fusion_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return instr["PC"] in self.hint_pcs

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return (
            "Fusion of sequential vector instructions failed "
            "because of non-vector instructions in-between. To "
            "turn on vector fusion, avoid putting non-vector "
            "instructions between vector ones."
        )
