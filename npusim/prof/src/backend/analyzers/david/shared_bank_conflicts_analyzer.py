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

from typing import List, Dict, Set, Tuple, Iterator, Optional
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


BankConflictItem = Tuple[int, int, int]


class DatabaseOperations:
    @staticmethod
    def iter_memory_access_events(supported_instrs: Set[str]):
        return database_handler.iter_rows(
            """
            SELECT ExecInstrId, InstrName, AccessedAddress
            FROM SharedPatterns
            WHERE InstrName IN {}
            ORDER BY ExecInstrId, InstrName
        """.format(tuple(supported_instrs))
        )

    @staticmethod
    def insert_bank_conflicts(data: List[BankConflictItem]) -> None:
        database_handler.executemany(
            """
        INSERT INTO
            UBBankConflicts (
                ExecInstrId,
                ReadConflicts,
                WriteConflicts
            )
        VALUES
        (?, ?, ?)
        """,
            data,
        )


class Ascend950SharedBankConflictsAnalyzer(IAnalyzer):
    BATCH_SIZE = 10_000
    # Reads scatter/gather and SIMT shared-memory load/store instructions.
    # RV_VGATHER2/SIMT_LDS = reads, RV_VSCATTER2/SIMT_STS = writes.
    # Bank conflicts occur when multiple threads access different blocks
    # within the same 5-bit bank group simultaneously.
    supported_read_instrs = {
        "RV_VGATHER2",
        "SIMT_LDS",
    }
    supported_write_instrs = {
        "RV_VSCATTER2",
        "SIMT_STS",
    }
    supported_instrs = supported_read_instrs | supported_write_instrs

    @staticmethod
    def get_name() -> str:
        return "ascend950_shared_bank_conflicts"

    @staticmethod
    def get_shared_bank_conflicts(addreses: List[int]) -> int:
        # Bank conflict detection algorithm:
        #
        #   UB memory address (byte-level):
        #   +----+----+----+----+----+----+----+----+----+-- ... --+
        #   | bit 31 ... bit 8 | b7 | b6 | b5 | b4 | b3 | b2 b1 b0|
        #   +----+----+----+----+----+----+----+----+----+-- ... --+
        #   bank group bit range: 7..3, block bit range: 2..0
        #   bank_group_index = (addr >> 3) & 0x1F -> 5 bits = 32 bank groups
        #   block_index      = addr >> 3             -> distinguishes 8B blocks
        #
        #   Conflict = N distinct blocks mapped to the same bank_group.
        #   Conflict count = max(N-1) across all bank groups.
        bank_group_map: Dict[int, Set[int]] = defaultdict(set)
        for address in addreses:
            bank_group_index = (address >> 3) & 0x1F
            block_index = address >> 3
            bank_group_map[bank_group_index].add(block_index)
        bank_conflict_count = 0
        for _, accessed_blocks in bank_group_map.items():
            if len(accessed_blocks) > 1:
                bank_conflict_count = max(len(accessed_blocks) - 1, bank_conflict_count)
        return bank_conflict_count

    def iter_bank_conflicts(self) -> Iterator[BankConflictItem]:
        cur_instr_id: Optional[int] = None
        cur_instr_name: Optional[str] = None
        cur_addresses: List[int] = []

        for row in DatabaseOperations.iter_memory_access_events(self.supported_instrs):
            instr_id = row["ExecInstrId"]
            instr_name = row["InstrName"]
            address = row["AccessedAddress"]

            if cur_instr_id is not None and (instr_id != cur_instr_id or instr_name != cur_instr_name):
                conflicts_count = self.get_shared_bank_conflicts(cur_addresses)
                if cur_instr_name in self.supported_read_instrs:
                    yield (cur_instr_id, conflicts_count, 0)
                elif cur_instr_name in self.supported_write_instrs:
                    yield (cur_instr_id, 0, conflicts_count)
                cur_addresses.clear()

            cur_instr_id = instr_id
            cur_instr_name = instr_name
            cur_addresses.append(address)

        if cur_instr_id is not None:
            conflicts_count = self.get_shared_bank_conflicts(cur_addresses)
            if cur_instr_name in self.supported_read_instrs:
                yield (cur_instr_id, conflicts_count, 0)
            elif cur_instr_name in self.supported_write_instrs:
                yield (cur_instr_id, 0, conflicts_count)

    def process(self) -> None:
        # Batch-insert pattern: accumulate up to BATCH_SIZE rows,
        # then flush to DB. Avoids per-row INSERT overhead.
        batch: List[BankConflictItem] = []
        for item in self.iter_bank_conflicts():
            batch.append(item)
            if len(batch) >= self.BATCH_SIZE:
                DatabaseOperations.insert_bank_conflicts(batch)
                batch.clear()
        if batch:
            DatabaseOperations.insert_bank_conflicts(batch)
            batch.clear()
