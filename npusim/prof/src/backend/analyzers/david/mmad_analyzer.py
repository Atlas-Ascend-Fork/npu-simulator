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
from dataclasses import dataclass
from typing import List, Optional

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams


@dataclass
class MmadParams:
    m: int
    k: int
    n: int
    matrix_a_dtype: Optional[str]
    matrix_b_dtype: Optional[str]
    matrix_c_dtype: Optional[str]
    enabled_mode: Optional[str]
    matrix_a_l0a_addr: int
    matrix_b_l0b_addr: int
    matrix_c_addr: int
    hw_checks_unit_flag: Optional[bool]
    hw_sets_unit_flag: Optional[bool]
    gemv_mode_enable: bool
    matrix_c_in_bias_table_buf: bool
    matrix_c_init_val: bool
    matrix_a_processed_bytes: int
    matrix_b_processed_bytes: int
    matrix_c_processed_bytes: int


class Ascend950MmadAnalyzer(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.instr_names = ("MMAD", "MMAD_MX")

    @staticmethod
    def get_name() -> str:
        return "ascend950_mmad_analyzer"

    def get_mmad_instrs(self) -> List[sqlite3.Row]:
        executed_instrs = database_handler.execute(
            """
        SELECT ExtendParams, ExecutedInstructions.ExecInstrId,
               ExecInstrName
        FROM ExecutedInstructions
        WHERE ExecInstrName IN {}
        ORDER BY ExecutedInstructions.ExecInstrId
        """.format(self.instr_names)
        )
        return executed_instrs.fetchall()

    def process_instruction(self, instr: sqlite3.Row) -> Optional[MmadParams]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        m = ep.m
        k = ep.k
        n = ep.n
        if m is None or k is None or n is None:
            return None
        return MmadParams(
            m=m,
            k=k,
            n=n,
            matrix_a_dtype=ep.matrix_a_dtype,
            matrix_b_dtype=ep.matrix_b_dtype,
            matrix_c_dtype=ep.matrix_c_dtype,
            enabled_mode=None,
            matrix_a_l0a_addr=0,
            matrix_b_l0b_addr=0,
            matrix_c_addr=0,
            hw_checks_unit_flag=None,
            hw_sets_unit_flag=None,
            gemv_mode_enable=False,
            matrix_c_in_bias_table_buf=False,
            matrix_c_init_val=False,
            matrix_a_processed_bytes=ep.matrix_a_processed_bytes or 0,
            matrix_b_processed_bytes=ep.matrix_b_processed_bytes or 0,
            matrix_c_processed_bytes=ep.matrix_c_processed_bytes or 0,
        )

    @staticmethod
    def add_mmad(instr_id: int, mmad_params: MmadParams) -> None:
        mmad_values = [
            instr_id,
            mmad_params.m,
            mmad_params.k,
            mmad_params.n,
            mmad_params.matrix_a_dtype,
            mmad_params.matrix_b_dtype,
            mmad_params.matrix_c_dtype,
            mmad_params.enabled_mode,
            mmad_params.matrix_a_l0a_addr,
            mmad_params.matrix_b_l0b_addr,
            mmad_params.matrix_c_addr,
            mmad_params.hw_checks_unit_flag,
            mmad_params.hw_sets_unit_flag,
            mmad_params.gemv_mode_enable,
            mmad_params.matrix_c_in_bias_table_buf,
            mmad_params.matrix_c_init_val,
            mmad_params.matrix_a_processed_bytes,
            mmad_params.matrix_b_processed_bytes,
            mmad_params.matrix_c_processed_bytes,
        ]
        database_handler.execute(
            f"""
            INSERT INTO MmadParams
            VALUES ({",".join("?" * len(mmad_values))})
            """,
            mmad_values,
        )

    def process(self) -> None:
        mmad_instrs = self.get_mmad_instrs()
        for instr in mmad_instrs:
            mmad_params = self.process_instruction(instr)
            if mmad_params is not None:
                self.add_mmad(instr["ExecInstrId"], mmad_params)
