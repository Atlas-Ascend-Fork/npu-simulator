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
from typing import List, Dict, Tuple

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrQueueType
from npusim.prof.src.backend.analyzers.base.region_tracing_analyzer_base import (
    RegionTracingAnalyzerBase,
    Region,
)


class Ascend950SIMTRegionTracingAnalyzer(RegionTracingAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.warp_pat = re.compile(r"\[warpId:\s*(?P<warp_id>[0-9]+)\]")

    @staticmethod
    def get_name() -> str:
        return "ascend950_simt_region_tracing_analyzer"

    def is_start(self, encoding: int) -> bool:
        # [32, 33] bits for position (start=10, end=11)
        return (encoding >> 32) & 3 == 2

    def is_end(self, encoding: int) -> bool:
        # [32, 33] bits for position (start=10, end=11)
        return (encoding >> 32) & 3 == 3

    def get_region_id(self, encoding: int) -> int:
        return (encoding >> 34) & 65535  # [34, 49] bits for region id

    @staticmethod
    def get_simt_region_instrs(core_id: int, core_type_id: int) -> List[sqlite3.Row]:
        instrs = database_handler.execute(
            """
            SELECT ExecutedInstructions.ExecInstrTickEnd,
                   ExecutedInstructions.ExecInstrName,
                   ExecutedInstructions.ExecInstrParams,
                   ExecutedInstructions.SourceInstrAddr,
                   SourceInstructions.SourceInstrEncoding
            FROM ExecutedInstructions
            LEFT JOIN SourceInstructions
            ON        ExecutedInstructions.SourceInstrAddr =
                      SourceInstructions.SourceInstrAddr
            WHERE ExecInstrCoreId = :core_id AND
                CoreTypeId = :core_type_id AND
                ExecInstrName LIKE 'SIMT_NOP%'
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
        region_stacks: Dict[Tuple[int, int], List[Region]] = {}
        regions: List[Region] = []
        for instr in instrs:
            encoding_str = instr["SourceInstrEncoding"]
            params = instr["ExecInstrParams"]
            if encoding_str is None or params is None:
                continue
            encoding = int(encoding_str, base=16)
            region_id = self.get_region_id(encoding)
            match = self.warp_pat.search(params)
            if match is None:
                continue
            warp_id = int(match["warp_id"])
            if self.is_start(encoding):
                if (region_id, warp_id) not in region_stacks:
                    region_stacks[region_id, warp_id] = []
                region_stacks[region_id, warp_id].append(
                    Region(
                        name=f"CustomRegion_{region_id}",
                        start_time=instr["ExecInstrTickEnd"],
                        end_time=0,
                        core_id=core_id,
                        core_type_id=core_type_id,
                        region_type_id=InstrQueueType.SIMT.value,
                        pipe_type_id=InstrQueueType.SIMT.value,
                        warp_id=warp_id,
                    )
                )
            if self.is_end(encoding):
                if (region_id, warp_id) not in region_stacks or len(region_stacks[region_id, warp_id]) == 0:
                    log.warning(f"Region `CustomRegion_{region_id}` (warpId: {{warp_id}}) has no start.")
                elif region_stacks[region_id, warp_id][-1].start_time > instr["ExecInstrTickEnd"]:
                    log.warning(
                        f"Region `CustomRegion_{region_id}` (warpId: "
                        f"{warp_id}) has start time "
                        f"({region_stacks[region_id, warp_id][-1].start_time})"
                        " more than end time "
                        f"({instr['ExecInstrTickEnd']})"
                    )
                    region_stacks[region_id, warp_id].pop()
                else:
                    region_stacks[region_id, warp_id][-1].end_time = instr["ExecInstrTickEnd"]
                    regions.append(region_stacks[region_id, warp_id].pop())
        for region in regions:
            self.insert_region(region)

    def process(self) -> None:
        unique_cores = self.get_unique_cores()
        for core in unique_cores:
            core_id = core["Id"]
            unique_core_types = self.get_unique_core_types(core_id)
            for core_type in unique_core_types:
                core_type_id = core_type["Id"]
                instrs = self.get_simt_region_instrs(core_id, core_type_id)
                self.process_regions(instrs, core_id, core_type_id)
