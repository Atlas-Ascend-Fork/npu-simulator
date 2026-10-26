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

import functools
import re
import struct
import sqlite3
from typing import List

from npusim.prof.src.backend.analyzers.basic_block_analyzer import BasicBlockAnalyzer
import npusim.prof.src.common.utils as utils


class Ascend950BasicBlockAnalyzer(BasicBlockAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self._branch_instr_processors.update(
            {
                "SIMT_BRANCH": self._process_simt_branch,
            }
        )

    @staticmethod
    def get_name() -> str:
        return "ascend950_basic_block"

    @classmethod
    @functools.lru_cache(maxsize=1)
    def get_simt_terminator_instrs(cls) -> List[str]:
        return [
            "SIMT_END",
            "SIMT_RET",
            "SIMT_CALL",
            "SIMT_CALLI",
            "SIMT_BRANCH",
            "SIMT_CALL_IND",
            "SIMT_BRANCH_IND",
        ]

    @classmethod
    @functools.lru_cache(maxsize=1)
    def get_terminator_instrs(cls) -> List[str]:
        instrs = super().get_terminator_instrs()
        instrs.extend(cls.get_simt_terminator_instrs())
        return instrs

    def _process_simt_branch(self, row: sqlite3.Row) -> None:
        addr = row["SourceInstrAddr"]
        text = row["SourceInstrText"]
        br_offset = self.extract_int_value(r"ofst:([0-9a-f]+)", text, 16)
        offset_value = struct.unpack("i", struct.pack("I", br_offset))[0]
        bb_start_address = addr + offset_value
        self.insert_bb(bb_start_address)
        self._jumps.append((addr, bb_start_address))
        # if jump is conditional there is an additional jump
        # to the next instruction (PC + instr_length)
        # unconditional branch - P7 or PZ register
        if not re.search(r"\bP(7|Z)\b", text):
            instr_length = utils.get_instr_length(text)
            self._jumps.append((addr, addr + instr_length))
