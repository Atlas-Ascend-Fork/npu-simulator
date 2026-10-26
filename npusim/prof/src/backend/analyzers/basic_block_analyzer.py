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

from dataclasses import astuple, dataclass
import logging as log
import functools
import re
from typing import Callable, Dict, List, Optional, Tuple
import bisect
import sqlite3

from npusim.prof.src.common.utils import get_hidumper_path
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
import npusim.prof.src.common.utils as utils


@dataclass
class BasicBlockInfo:
    addr: int
    length: int
    name: str

    def __str__(self) -> str:
        return f"{self.name}"


class BasicBlockAnalyzer(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self._basic_blocks: Dict[int, BasicBlockInfo] = {}
        self._jumps: List[Tuple[Optional[int], int]] = []
        self._basic_block_relations: List[Tuple[int, int]] = []
        self._branch_instr_processors: Dict[str, Callable] = {
            "JUMP": self._process_jump,
            "JUMPC": self._process_jump,
            "JUMPCMP": self._process_jump,
        }

    @staticmethod
    def get_name() -> str:
        return "basic_block"

    @classmethod
    @functools.lru_cache(maxsize=1)
    def get_scalar_terminator_instrs(cls) -> List[str]:
        return [
            # v220
            "END",
            "JUMP",
            "JUMPC",
            "JUMPR",
            "CALL",
            "CALLR",
            "RET",
            "LOOP",
            "ENDLOOP",
            "JUMPCMP",
        ]

    @classmethod
    @functools.lru_cache(maxsize=1)
    def get_terminator_instrs(cls) -> List[str]:
        return cls.get_scalar_terminator_instrs()

    def insert_bb(self, start_address: int) -> None:
        # Basic blocks are keyed by start address (first instruction's PC).
        # Each block spans from its start to the next block's start address.
        if start_address not in self._basic_blocks:
            self._basic_blocks[start_address] = BasicBlockInfo(
                start_address,
                0,
                "",
            )

    @functools.lru_cache()
    def get_bb_start_pos(self, terminator_addr: int) -> Optional[int]:
        # Given an instruction address, find which basic block it belongs to.
        # Returns the start address of the containing basic block.
        # (e.g. addr 0x105 with blocks [0x100, 0x108] -> returns 0x100)
        sorted_keys = sorted(self._basic_blocks)
        idx = bisect.bisect_right(sorted_keys, terminator_addr)
        if idx == 0:
            return None
        return sorted_keys[idx - 1]

    def process(self) -> None:
        # CFG (Control Flow Graph) construction from static disassembly:
        #
        # 1. Scan instructions sequentially; a new basic block starts after
        #    every branch/jump terminator instruction (JUMP, JUMPC, CALL, etc.)
        # 2. Branch targets (extracted from instruction operands) also start
        #    BBs.
        # 3. After identifying all BB starts, compute lengths and names.
        # 4. Build predecessor/successor edges from jumps and fall-throughs.
        if get_hidumper_path() is not None:
            log.debug("Skipping redundant basic block analyzer (hidumper tool is used for disassembling)")
            return
        cur = database_handler.execute("""
        SELECT SourceInstrAddr, SourceInstrText,
            SourceInstrEncoding
        FROM SourceInstructions
        ORDER BY SourceInstrAddr
        """)
        # assume that the first analyzed instruction has default instruction
        # length
        prev_instr_length = 4
        prev_instr_addr = None
        rows = cur.fetchall()
        source_length = 0
        for row in rows:
            addr = int(row["SourceInstrAddr"])
            text = str(row["SourceInstrText"])
            instr_name = text.split(" ")[0].split(".", 1)[0]
            instr_length = utils.get_instr_length(text)
            bb_start_address = addr + instr_length
            source_length = max(source_length, bb_start_address)

            # Traverse all instruction and create a new basic block each time
            # when branch/jump instruction is met
            if prev_instr_addr is None:
                self.insert_bb(addr)
                prev_instr_addr = -prev_instr_length
            if addr < prev_instr_addr + prev_instr_length:
                # This instruction overlaps with previous (multi-word
                # instruction encoding like VF_SIMT = 8 bytes).
                # Skip the continuation word.
                continue
            if instr_name in self.get_terminator_instrs():
                self.insert_bb(bb_start_address)
            prev_instr_length = instr_length
            prev_instr_addr = addr

            # Process specific branch/jump instructions
            # To build up new basic blocks that are judging only
            # by instruction names
            # e.g. build a new basic block in a place where offset
            if instr_name in self._branch_instr_processors.keys():
                self._branch_instr_processors[instr_name](row)
        sorted_bb_start = sorted(self._basic_blocks)
        # Assign sequential names: "Basic Block 0", "Basic Block 1", ...
        for idx, k in enumerate(sorted_bb_start):
            self._basic_blocks[k].name = f"Basic Block {idx}"

        # Compute block lengths: each block ends where the next one begins.
        # Last block ends at the highest instruction address + its length.
        for idx, k in enumerate(sorted_bb_start[:-1]):
            bb_start = sorted_bb_start[idx]
            bb_end = sorted_bb_start[idx + 1]
            self._basic_blocks[k].length = bb_end - bb_start
        self._basic_blocks[sorted_bb_start[-1]].length = source_length - sorted_bb_start[-1]

        # Finally, store all basic block information to the database
        self._insert_basic_blocks()

        # Store jumps information:
        # - Source instruction address
        # - Potential jump destination (for both conditional and
        # unconditional branches)
        self._insert_jumps()

        # Handle basic blocks where there is no terminator instruction (by ISA
        # definition). E.g. basic blocks that end with computation or
        # load/store instruction without an explicit jump anywhere else
        # This needs to be done, because even though there is no explicit jump
        # current basic block has the next basic block as its successor,
        # the next basic block has current basic block as a predecessor
        # Fall-through edges: when a basic block ends with a non-terminator
        # instruction, execution flows to the next block implicitly.
        # Add a virtual jump edge to capture this in the CFG.
        last_instr = None
        for row in rows:
            addr = int(row["SourceInstrAddr"])
            if last_instr:
                text = last_instr["SourceInstrText"]
                instr_name = text.split(" ")[0]
            if addr in self._basic_blocks and last_instr and instr_name not in self.get_terminator_instrs():
                # Previous instruction was a final instruction in basic block
                prev_bb = self.get_bb_start_pos(last_instr["SourceInstrAddr"])
                self._jumps.append((prev_bb, addr))
            last_instr = row

        # Build basic block relations mapping to get
        # basic blocks' predecessors and successors
        errors_detected = False
        for jump_start, jump_end in list(set(self._jumps)):
            start_bb = self.get_bb_start_pos(jump_start)
            if start_bb is None:
                errors_detected = True
                continue
            self._basic_block_relations.append((start_bb, jump_end))
        if errors_detected:
            log.warning("Broken basic block mapping is detected, CFG may be not accurate")

        # Store basic block relations information
        self._insert_basic_block_relations()

    def cleanup(self) -> None:
        self._basic_blocks.clear()
        self._jumps.clear()
        self._basic_block_relations.clear()

    def _process_jump(self, row: sqlite3.Row) -> None:
        addr = int(row["SourceInstrAddr"])
        text = str(row["SourceInstrText"])
        start_pc = database_handler.get_start_pc()
        # Instruction was not executed by simulator
        if "PC:" not in text:
            return
        jump_value = self.extract_int_value(r"PC:(?:0x)?([0-9a-f]+)", text, 16)
        bb_start_address = jump_value - start_pc
        self.insert_bb(bb_start_address)
        self._jumps.append((addr, bb_start_address))
        # if jump is conditional there is an additional jump
        # to the next instruction (PC + instr_length)
        if re.search(r"JUMP(C|CMP)\s", text) or re.search(r"jumpci\s", text):
            instr_length = utils.get_instr_length(text)
            self._jumps.append((addr, addr + instr_length))

    def _insert_basic_blocks(self) -> None:
        database_handler.executemany(
            """
        INSERT INTO BasicBlocks (
            SourceInstrAddr,
            BBLength,
            BBName)
        VALUES (?, ?, ?)
        """,
            [astuple(bb_info) for bb_info in self._basic_blocks.values()],
        )

    def _insert_basic_block_relations(self) -> None:
        database_handler.executemany(
            """
        INSERT INTO BasicBlocksRelations (
            StartBlockId,
            EndBlockId)
        VALUES (?, ?)
        """,
            list(set(self._basic_block_relations)),
        )

    def _insert_jumps(self) -> None:
        database_handler.executemany(
            """
        INSERT INTO JumpsDescription (Src, Dst) VALUES (?, ?)
        """,
            list(set(self._jumps)),
        )
