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
import logging as log
import re
from typing import List, Dict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrQueueType
from npusim.prof.src.backend.analyzers.base.region_tracing_analyzer_base import (
    RegionTracingAnalyzerBase,
    Region,
)


class Ascend950SIMDRegionTracingAnalyzer(RegionTracingAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.reg_mask_pat = re.compile(r"\Sn\[\d+\]=([0xA-Fa-f0-9]+)")
        self.reg_params_pat = re.compile(r"\Sn2\[\d+\]=([0xA-Fa-f0-9]+)")

    @staticmethod
    def get_name() -> str:
        return "ascend950_simd_region_tracing_analyzer"

    def is_start(self, sn2: int) -> bool:
        # [32, 33] bits in value is [0, 1] bits in Sn2
        # for position (start=10, end=11)
        return sn2 & 3 == 2

    def is_end(self, sn2: int) -> bool:
        # [32, 33] bits in value is [0, 1] bits in Sn2
        # for position (start=10, end=11)
        return sn2 & 3 == 3

    def get_region_id(self, sn2: int) -> int:
        # [34, 41] bits in value is [2, 9] bits in Sn2 for region id
        return (sn2 >> 2) & 65535

    def is_region_mask(self, sn: int) -> bool:
        return sn & 65535 == 21332

    @staticmethod
    def get_simd_region_instrs(core_id: int, core_type_id: int) -> List[sqlite3.Row]:
        instrs = database_handler.execute(
            """
            SELECT ExecInstrTickEnd, ExecInstrName, ExecInstrParams
            FROM ExecutedInstructions
            WHERE ExecInstrCoreId = :core_id AND
                CoreTypeId = :core_type_id AND
                ExecInstrName LIKE 'RV_VBR%'
            ORDER BY ExecInstrTickEnd
            """,
            {"core_id": core_id, "core_type_id": core_type_id},
        )
        result: List[sqlite3.Row] = instrs.fetchall()
        return result

    def process_regions(
        self,
        instrs: List[sqlite3.Row],
        core_id: int,
        core_type_id: int,
    ) -> None:
        # Region tracing via Sn/Sn2 register values in RV_VBR instructions.
        # Uses a stack per region_id to handle nested regions:
        #   - Sn2 bits [0:1]=2 -> push new region start onto stack
        #   - Sn2 bits [0:1]=3 -> pop region from stack (end)
        #   - Sn2 bits [2:9]   -> region identifier
        region_stacks: Dict[int, List[Region]] = {}
        regions: List[Region] = []
        for instr in instrs:
            params = instr["ExecInstrParams"]
            if params is None:
                continue
            sn_match = self.reg_mask_pat.search(params)
            assert sn_match is not None, "RV_VBR instruction without Sn value"
            sn = int(sn_match[1], base=16)
            if not self.is_region_mask(sn):
                continue
            sn2_match = self.reg_params_pat.search(params)
            assert sn2_match is not None, (
                "RV_VBR instruction \
                without Sn2 value"
            )
            sn2 = int(sn2_match[1], base=16)
            reg_id = self.get_region_id(sn2)
            if self.is_start(sn2):
                if reg_id not in region_stacks:
                    region_stacks[reg_id] = []
                region_stacks[reg_id].append(
                    Region(
                        name=f"CustomRegion_{reg_id}",
                        start_time=instr["ExecInstrTickEnd"],
                        end_time=0,
                        core_id=core_id,
                        core_type_id=core_type_id,
                        region_type_id=InstrQueueType.SIMD.value,
                        pipe_type_id=InstrQueueType.SIMD.value,
                        warp_id=-1,
                    )
                )
            if self.is_end(sn2):
                if reg_id not in region_stacks or len(region_stacks[reg_id]) == 0:
                    log.warning(f"Region `CustomRegion_{reg_id}` has no start.")
                elif region_stacks[reg_id][-1].start_time > instr["ExecInstrTickEnd"]:
                    log.warning(
                        f"Region `CustomRegion_{reg_id}` has start time "
                        f"({region_stacks[reg_id][-1].start_time})"
                        f" more than end time ({instr['ExecInstrTickEnd']})"
                    )
                    region_stacks[reg_id].pop()
                else:
                    region_stacks[reg_id][-1].end_time = instr["ExecInstrTickEnd"]
                    regions.append(region_stacks[reg_id].pop())
        for region in regions:
            self.insert_region(region)

    def process(self) -> None:
        unique_cores = self.get_unique_cores()
        for core in unique_cores:
            core_id = core["Id"]
            unique_core_types = self.get_unique_core_types(core_id)
            for core_type in unique_core_types:
                core_type_id = core_type["Id"]
                instrs = self.get_simd_region_instrs(core_id, core_type_id)
                self.process_regions(instrs, core_id, core_type_id)
