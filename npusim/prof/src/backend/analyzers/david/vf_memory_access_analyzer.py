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
from typing import Dict, Iterator, List

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


class Ascend950VFMemoryAccessAnalyzer(IAnalyzer):
    # Maps SIMD memory accesses to their parent VF instruction.
    # Scans VF instructions and their inner load/store (RVECLD/RVECST) ops,
    # then for each load/store, finds which VF instruction's tick range
    # contains it. Saves the mapping so memory accesses can be attributed
    # to specific VF instructions in downstream analysis/reports.
    BATCH_SIZE = 10000

    def __init__(self) -> None:
        super().__init__()
        self.instr_to_vf: Dict[int, int] = {}

    @staticmethod
    def get_name() -> str:
        return "ascend950_vf_memory_access_analyzer"

    @staticmethod
    def get_vf_instrs(core_id: int, core_type_id: int) -> List[sqlite3.Row]:
        return database_handler.execute(
            """
        SELECT * FROM ExecutedInstructions
        WHERE ExecutedInstructions.ExecInstrName = "VF"
        AND ExecutedInstructions.ExecInstrCoreId = :core_id
        AND ExecutedInstructions.CoreTypeId = :core_type_id
        ORDER BY ExecInstrTickStart
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchall()

    @staticmethod
    def iter_vf_ld_st_instrs(core_id: int, core_type_id: int) -> Iterator[sqlite3.Row]:
        return database_handler.iter_rows(
            f"""
        SELECT * FROM ExecutedInstructions
        WHERE ExecutedInstructions.ExecInstrCoreId = :core_id
        AND ExecutedInstructions.CoreTypeId = :core_type_id
        AND (InstrTypeId = {InstrType.RVECLD.value}
             OR InstrTypeId = {InstrType.RVECST.value})
        ORDER BY ExecInstrTickStart
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        )

    @staticmethod
    def iter_vf_mem_accesses(core_id: int, core_type_id: int) -> Iterator[sqlite3.Row]:
        return database_handler.iter_rows(
            f"""
        SELECT * FROM MemoryRWAccesses
        LEFT JOIN ExecutedInstructions
        ON ExecutedInstructions.ExecInstrId=MemoryRWAccesses.ExecInstrId
        WHERE CoreId=:core_id
        AND CoreTypeId = :core_type_id
        AND (InstrTypeId = {InstrType.RVECLD.value}
             OR InstrTypeId = {InstrType.RVECST.value})
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        )

    def process_instrs(self, vf_instrs: List[sqlite3.Row], ld_st_instrs: Iterator[sqlite3.Row]) -> None:
        for instr in ld_st_instrs:
            for vf in vf_instrs:
                if (
                    vf["ExecInstrTickStart"] <= instr["ExecInstrTickStart"] <= vf["ExecInstrTickEnd"]
                    and vf["ExecInstrTickStart"] <= instr["ExecInstrTickEnd"] <= vf["ExecInstrTickEnd"]
                ):
                    self.instr_to_vf[instr["ExecInstrId"]] = vf["ExecInstrId"]
                    break

    def save_vf_mem_accesses(self, mem_accesses: Iterator[sqlite3.Row]) -> None:
        insert_sql = """
            INSERT INTO VFMemoryRWAccesses
            (AccessedAddress, AccessMode, AccessTime,
             MemoryType, ExecInstrId, CoreId)
            VALUES
            (?, ?, ?, ?, ?, ?)
            """
        batch: List[tuple] = []
        for item in mem_accesses:
            if item["ExecInstrId"] not in self.instr_to_vf:
                continue
            batch.append(
                (
                    item["AccessedAddress"],
                    item["AccessMode"],
                    item["AccessTime"],
                    item["MemoryType"],
                    self.instr_to_vf[item["ExecInstrId"]],
                    item["CoreId"],
                )
            )
            if len(batch) >= self.BATCH_SIZE:
                database_handler.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            database_handler.executemany(insert_sql, batch)

    def process(self) -> None:
        for core_id in self.get_active_cores():
            for core_type_id in self.get_active_core_types():
                vf_instrs = self.get_vf_instrs(core_id, core_type_id)
                if not vf_instrs:
                    continue
                ld_st_instrs = self.iter_vf_ld_st_instrs(core_id, core_type_id)
                self.process_instrs(vf_instrs, ld_st_instrs)
                mem_accesses = self.iter_vf_mem_accesses(core_id, core_type_id)
                self.save_vf_mem_accesses(mem_accesses)

    def cleanup(self) -> None:
        self.instr_to_vf.clear()
