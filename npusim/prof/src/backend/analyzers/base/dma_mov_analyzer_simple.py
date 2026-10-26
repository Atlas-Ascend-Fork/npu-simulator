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

from dataclasses import dataclass
from typing import Optional
from npusim.prof.src.backend.analyzers.base.dma_mov_analyzer_base import (
    DMAMovAnalyzerBase,
    DMAParamsInfo,
)
from npusim.prof.src.backend.database import database_handler


@dataclass
class DMAMovSimpleParamsInfo(DMAParamsInfo):
    burst_len: int
    burst_number: int
    src_stride: int
    dst_stride: int
    # is specific to some instructions (for example, MOV_L1_TO_UB)
    sub_block_id: Optional[int] = None


class DMAMovAnalyzerSimple(DMAMovAnalyzerBase):
    @classmethod
    def save_dma_params_info(cls, instr_id: int, params_info: DMAParamsInfo) -> None:
        assert isinstance(params_info, DMAMovSimpleParamsInfo)
        database_handler.execute(
            """
            INSERT INTO DmaMovSimpleParams
            (ExecInstrId,
             BurstLength,
             BurstNumber,
             SrcStride,
             DstStride,
             SubBlockId
            )
            VALUES
            (:exec_instr_id,
             :burst_len,
             :burst_number,
             :src_stride,
             :dst_stride,
             :sub_block_id
            )
        """,
            {
                "exec_instr_id": instr_id,
                "burst_len": params_info.burst_len,
                "burst_number": params_info.burst_number,
                "src_stride": params_info.src_stride,
                "dst_stride": params_info.dst_stride,
                "sub_block_id": params_info.sub_block_id,
            },
        )
