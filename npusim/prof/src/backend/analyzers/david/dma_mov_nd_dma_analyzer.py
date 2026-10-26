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
from typing import Optional

from npusim.prof.src.common.extend_params import ExtendParams
from npusim.prof.src.backend.analyzers.base.dma_mov_analyzer_base import (
    DMAMovAnalyzerBase,
    DMAInstrInfo,
    DMAParamsInfo,
)
from npusim.prof.src.backend.database import database_handler


@dataclass
class DMANdDmaParamsInfo(DMAParamsInfo):
    loop0_size: int
    loop1_size: int
    loop2_size: int
    loop3_size: int
    loop4_size: int
    loop0_lp_size: int
    loop0_rp_size: int


class Ascend950DMAMovNdDmaAnalyzer(DMAMovAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.instr_params["ND_DMA"] = {}

    @staticmethod
    def get_name() -> str:
        return "ascend950_dma_mov_nd_dma_analyzer"

    def process_instruction(self, instr: sqlite3.Row) -> Optional[DMAInstrInfo]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        loop0_size = ep.loop0_size
        loop1_size = ep.loop1_size
        loop2_size = ep.loop2_size
        loop3_size = ep.loop3_size
        loop4_size = ep.loop4_size
        loop0_lp_size = ep.loop0_lp_size
        loop0_rp_size = ep.loop0_rp_size
        processed_bytes = ep.processed_bytes
        if any(
            v is None
            for v in (
                loop0_size,
                loop1_size,
                loop2_size,
                loop3_size,
                loop4_size,
                loop0_lp_size,
                loop0_rp_size,
            )
        ):
            return None
        return DMAInstrInfo(
            DMANdDmaParamsInfo(
                loop0_size,
                loop1_size,
                loop2_size,
                loop3_size,
                loop4_size,
                loop0_lp_size,
                loop0_rp_size,
            ),
            processed_bytes,
        )

    @classmethod
    def save_dma_params_info(cls, instr_id: int, params_info: DMAParamsInfo) -> None:
        assert isinstance(params_info, DMANdDmaParamsInfo)
        database_handler.execute(
            """
            INSERT INTO DmaMovNdDmaParams
            (ExecInstrId,
             Loop0Size,
             Loop1Size,
             Loop2Size,
             Loop3Size,
             Loop4Size,
             Loop0LpSize,
             Loop0RpSize)
            VALUES
            (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                instr_id,
                params_info.loop0_size,
                params_info.loop1_size,
                params_info.loop2_size,
                params_info.loop3_size,
                params_info.loop4_size,
                params_info.loop0_lp_size,
                params_info.loop0_rp_size,
            ),
        )
