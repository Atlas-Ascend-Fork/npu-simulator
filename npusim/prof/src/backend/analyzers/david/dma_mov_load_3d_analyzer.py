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
class DMAMovLoad3dParamsInfo(DMAParamsInfo):
    m_step: int
    k_step: int
    dst: str


class Ascend950DMAMovLoad3DAnalyzer(DMAMovAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.instr_params["LOAD_L1_TO_DST_3DV2"] = {}

    @staticmethod
    def get_name() -> str:
        return "ascend950_dma_mov_load_3d_analyzer"

    def process_instruction(self, instr: sqlite3.Row) -> Optional[DMAInstrInfo]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        m_step = ep.m_step
        k_step = ep.k_step
        processed_bytes = ep.processed_bytes
        dst = ep.dst_mem
        if any(v is None for v in (m_step, k_step, dst)):
            return None
        return DMAInstrInfo(
            DMAMovLoad3dParamsInfo(m_step, k_step, dst),
            processed_bytes,
        )

    @classmethod
    def save_dma_params_info(cls, instr_id: int, params_info: DMAParamsInfo) -> None:
        assert isinstance(params_info, DMAMovLoad3dParamsInfo)
        database_handler.execute(
            """
            INSERT INTO DmaMovLoad3dParams
            (ExecInstrId,
             MStep,
             KStep,
             Dst)
            VALUES
            (?, ?, ?, ?)
        """,
            (
                instr_id,
                params_info.m_step,
                params_info.k_step,
                params_info.dst,
            ),
        )
