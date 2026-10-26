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

from npusim.prof.src.common.extend_params import ExtendParams
from npusim.prof.src.backend.analyzers.base.dma_mov_analyzer_base import DMAInstrInfo
from npusim.prof.src.backend.analyzers.base.dma_mov_analyzer_simple import (
    DMAMovAnalyzerSimple,
    DMAMovSimpleParamsInfo,
)


class Ascend950DMAMovUbAnalyzer(DMAMovAnalyzerSimple):
    def __init__(self) -> None:
        super().__init__()
        self.instr_params["MOV_UB_TO_L1"] = {}
        self.instr_params["MOV_L1_TO_UB"] = {}
        self.instr_params["MOV_UB_TO_UB"] = {}

    @staticmethod
    def get_name() -> str:
        return "ascend950_dma_mov_ub_analyzer"

    def process_instruction(self, instr: sqlite3.Row) -> Optional[DMAInstrInfo]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        burst_len = ep.burst_len
        burst_number = ep.burst_number
        src_stride = ep.src_stride
        dst_stride = ep.dst_stride
        processed_bytes = ep.processed_bytes
        sub_block_id = ep.sub_block_id
        if any(v is None for v in (burst_len, burst_number, src_stride, dst_stride)):
            return None
        return DMAInstrInfo(
            DMAMovSimpleParamsInfo(
                burst_len,
                burst_number,
                src_stride,
                dst_stride,
                sub_block_id,
            ),
            processed_bytes,
        )
