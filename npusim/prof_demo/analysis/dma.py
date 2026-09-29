#!/usr/bin/env python3
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
"""DMA traffic consumed by instruction traces and summary bandwidth."""

from npusim.prof_demo.database import database_handler


DMA_INSTRS = (
    "MOV_SRC_TO_DST_ALIGNv2",
    "MOV_OUT_TO_L1_MULTI_ND2NZ",
    "MOV_OUT_TO_L1_MULTI_DN2NZ",
    "LOAD_2Dv2",
    "LOAD_MX_2Dv2",
    "MOV_UB_TO_L1",
    "MOV_L1_TO_UB",
    "MOV_UB_TO_UB",
    "ND_DMA",
    "LOAD_L1_TO_DST_3DV2",
)


class DmaAnalyzer:
    def process(self):
        placeholders = ",".join("?" for _ in DMA_INSTRS)
        rows = database_handler.iter_rows(
            f"""
            SELECT EI.ExecInstrId, EP.processed_bytes,
                   EI.ExecInstrTickStart, EI.ExecInstrTickEnd
            FROM sim.ExecutedInstructions AS EI
            JOIN ExtendParamsProjection AS EP ON EP.ExecInstrId=EI.ExecInstrId
            WHERE EI.ExecInstrName IN ({placeholders}) AND EP.processed_bytes IS NOT NULL
            ORDER BY EI.ExecInstrId
        """,
            DMA_INSTRS,
        )
        frequency_hz = float(database_handler.get_frequency_ghz()) * 1.0e9
        output = []
        for exec_id, processed_bytes, start, end in rows:
            traffic = 0.0
            if end > start:
                seconds = float(end - start) / frequency_hz
                traffic = float(int(processed_bytes)) / seconds / (1024.0**3)
            output.append((exec_id, int(processed_bytes), traffic))
        database_handler.executemany("INSERT INTO DmaMovProcessedBytes VALUES (?, ?, ?)", output)
