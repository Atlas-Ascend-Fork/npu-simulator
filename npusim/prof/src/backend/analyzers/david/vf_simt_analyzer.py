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
from dataclasses import dataclass, astuple
from typing import List

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.utils import extract_k_bits


@dataclass
class VfSimtInvocation:
    vf_simt_addr: int
    kernel_addr: int
    thread_dim_x: int
    thread_dim_y: int
    thread_dim_z: int
    register_num: int
    code_size_bytes: int


class Ascend950VfSimtAnalyzer(IAnalyzer):
    # Extracts VF_SIMT invocation metadata from instruction logs.
    # VF_SIMT launches a SIMT kernel on vector threads. Each invocation
    # encodes thread dimensions and register usage in the XM register:
    #
    #   XM register (64-bit):
    #   +--------+--------+--------+--------+
    #   | dim_z  | dim_y  | dim_x  |reg_num |
    #   | 12b@48 | 12b@32 | 12b@16 | 8b@0   |
    #   +--------+--------+--------+--------+
    #
    # Code size is extracted from the instruction encoding bits [37:52].
    def __init__(self) -> None:
        super().__init__()
        self._simt_invocations: List[VfSimtInvocation] = []

    @staticmethod
    def get_name() -> str:
        return "vf_simt"

    @staticmethod
    def _get_executed_vfsimts() -> List[sqlite3.Row]:
        return database_handler.execute(
            """
        SELECT
        ExecutedInstructions.SourceInstrAddr,
        ExecutedInstructions.ExecInstrName,
        ExecutedInstructions.ExecInstrParams,
        SourceInstructions.SourceInstrEncoding
        FROM ExecutedInstructions
        LEFT JOIN SourceInstructions
        ON        ExecutedInstructions.SourceInstrAddr =
                  SourceInstructions.SourceInstrAddr
        WHERE ExecutedInstructions.ExecInstrName = :vf_simt
        GROUP BY ExecutedInstructions.SourceInstrAddr
        ORDER BY ExecutedInstructions.SourceInstrAddr
        """,
            {"vf_simt": "VF_SIMT"},
        ).fetchall()

    def _process_row(self, row: sqlite3.Row) -> None:
        addr = row["SourceInstrAddr"]
        params = row["ExecInstrParams"]
        encoding_str = row["SourceInstrEncoding"]
        if params is None or encoding_str is None:
            return
        encoding = int(encoding_str, 16)

        kernel_addr = self.extract_int_value(r"XN:X[0-9]+=([0-9a-fx]+)", params, base=16)
        mask = self.extract_int_value(r"XM:X[0-9]+=([0-9a-fx]+)", params, base=16)
        take_12_bits = (1 << 12) - 1
        take_8_bits = (1 << 8) - 1
        thread_dim_x = (mask >> (16 * 0)) & take_12_bits
        thread_dim_y = (mask >> (16 * 1)) & take_12_bits
        thread_dim_z = (mask >> (16 * 2)) & take_12_bits
        reg_num = (mask >> (16 * 3)) & take_8_bits
        code_size_bytes = extract_k_bits(encoding, 37, 16) * 4
        self._simt_invocations.append(
            VfSimtInvocation(
                addr,
                kernel_addr,
                thread_dim_x,
                thread_dim_y,
                thread_dim_z,
                reg_num,
                code_size_bytes,
            )
        )

    def process(self) -> None:
        for row in self._get_executed_vfsimts():
            self._process_row(row)
        self.insert_kernel_metrics()

    def cleanup(self) -> None:
        self._simt_invocations.clear()

    def insert_kernel_metrics(self) -> None:
        database_handler.executemany(
            """
        INSERT INTO
            VfSimtInvocations (
                VfSimtAddr,
                KernelAddr,
                ThreadDimX,
                ThreadDimY,
                ThreadDimZ,
                RegisterNum,
                CodeSizeBytes
            )
        VALUES
        (?, ?, ?, ?, ?, ?, ?)
        """,
            [astuple(x) for x in self._simt_invocations],
        )
