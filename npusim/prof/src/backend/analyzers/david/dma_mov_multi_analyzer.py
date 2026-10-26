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
from typing import Optional
from dataclasses import dataclass

from npusim.prof.src.common.extend_params import ExtendParams
from npusim.prof.src.backend.analyzers.base.dma_mov_analyzer_base import (
    DMAMovAnalyzerBase,
    DMAInstrInfo,
    DMAParamsInfo,
)
from npusim.prof.src.backend.database import database_handler


@dataclass
class DMAMovMultiParamsInfo(DMAParamsInfo):
    n: int
    d: int


class Ascend950DMAMovMultiAnalyzer(DMAMovAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.instr_params["MOV_OUT_TO_L1_MULTI_ND2NZ"] = {}
        self.instr_params["MOV_OUT_TO_L1_MULTI_DN2NZ"] = {}

    @staticmethod
    def get_name() -> str:
        return "ascend950_dma_mov_multi_analyzer"

    def process_instruction(self, instr: sqlite3.Row) -> Optional[DMAInstrInfo]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        n = ep.n
        d = ep.d
        processed_bytes = ep.processed_bytes
        if n is None or d is None:
            return None
        return DMAInstrInfo(
            DMAMovMultiParamsInfo(n, d),
            processed_bytes,
        )

    @classmethod
    def save_dma_params_info(cls, instr_id: int, params_info: DMAParamsInfo) -> None:
        assert isinstance(params_info, DMAMovMultiParamsInfo)
        database_handler.execute(
            """
            INSERT INTO DmaMovMultiParams
            (ExecInstrId,
             N,
             D)
            VALUES
            (?, ?, ?)
        """,
            (
                instr_id,
                params_info.n,
                params_info.d,
            ),
        )
