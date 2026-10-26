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
from typing import Dict, List

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.common.biprof_constants import (
    DAVID_CACHE_LINE_SIZE,
    MAX_SIMT_THREADS_PER_WARP,
    RELTIME_THRESHOLD,
)
from npusim.prof.src.common.extend_params import ExtendParams


class MemoryAccessHint(IHint):
    # Ported from biprof: flags SIMT loads/stores whose warp access spans
    # more cache lines than its data needs (non-sequential global memory
    # access). In the biprof logs the dtype came from the instruction
    # params (ldg/stg type maps); with the contract DB the dtype is the
    # access_btype ExtendParams field, so a PC -> dtype map is built once
    # in __init__ (the MixGMHint pattern) and the instruction names are
    # SIMT_LDG/SIMT_STG, as logged by the simulator.

    msg = (
        "Inefficient access to global memory detected. Consider using sequential memory access for better performance."
    )

    def __init__(self) -> None:
        super().__init__()
        self.dtypes: Dict[int, str] = {}
        rows = database_handler.execute(
            """
        SELECT SourceInstrAddr AS PC, ExtendParams AS Params
        FROM ExecutedInstructions
        WHERE ExecInstrName IN ('SIMT_LDG', 'SIMT_STG')
        GROUP BY SourceInstrAddr
        """
        ).fetchall()
        for row in rows:
            dtype = ExtendParams.parse(row["Params"]).access_btype
            if dtype is not None:
                self.dtypes[row["PC"]] = dtype

    @staticmethod
    def get_name() -> str:
        return "v310_memory_access_hint"

    @classmethod
    def get_instruction_hint_text(cls, instr: sqlite3.Row) -> str:
        return cls.msg

    @classmethod
    def get_sourceline_hint_text(cls, sourceline_instrs: List[sqlite3.Row]) -> str:
        return cls.msg

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        instr_name = str(instr["InstrName"])
        is_ldg_or_stg = instr_name.startswith(("SIMT_LDG", "SIMT_STG"))
        reltime = instr["RelTime"]
        mem_access_stride = instr["MemAccessStride"]

        if is_ldg_or_stg:
            dtype = self.dtypes.get(instr["PC"])
            if dtype is None:
                return False
            dtype_size = IAnalyzer.get_dtype_size(dtype)
            data_size = dtype_size * MAX_SIMT_THREADS_PER_WARP
            count = (data_size + DAVID_CACHE_LINE_SIZE - 1) // DAVID_CACHE_LINE_SIZE

        return (
            is_ldg_or_stg
            and mem_access_stride is not None
            and reltime is not None
            and float(reltime) >= RELTIME_THRESHOLD
            and float(mem_access_stride).is_integer()
            and instr["CacheLineCount"] is not None
            and count < instr["CacheLineCount"]
        )

    def sourceline_condition(self, sourceline_instrs: List[sqlite3.Row]) -> bool:
        reltime_sum = 0
        for instr in sourceline_instrs:
            instr_name = str(instr["InstrName"])
            is_ldg_or_stg = instr_name.startswith(("SIMT_LDG", "SIMT_STG"))
            mem_access_stride = instr["MemAccessStride"]
            if (
                is_ldg_or_stg
                and mem_access_stride is not None
                and float(mem_access_stride).is_integer()
                and float(mem_access_stride) > 1.0
            ):
                reltime_sum += instr["RelTime"]
        return reltime_sum >= RELTIME_THRESHOLD
