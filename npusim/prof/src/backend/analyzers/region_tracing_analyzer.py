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
import re
import logging as log
from typing import List, Dict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import InstrQueueType
from npusim.prof.src.backend.analyzers.base.region_tracing_analyzer_base import (
    RegionTracingAnalyzerBase,
    Region,
)


class RegionTracingAnalyzer(RegionTracingAnalyzerBase):
    def __init__(self) -> None:
        super().__init__()
        self.pattern = re.compile(r"XT:X[\d]+=(0[x]*[\da-fA-F]*)")

    @staticmethod
    def get_name() -> str:
        return "region_tracing"

    # Pipe name templates for each instruction queue type
    @staticmethod
    def get_pipe_template(pipe: InstrQueueType) -> str:
        if pipe == InstrQueueType.SCALAR:
            return "SCALAR"
        if pipe == InstrQueueType.SIMD:
            return "VEC"
        if pipe == InstrQueueType.CUBE:
            return "CUBE"
        if pipe == InstrQueueType.MTE1:
            return "MTE1"
        if pipe == InstrQueueType.MTE2:
            return "MTE2"
        if pipe == InstrQueueType.MTE3:
            return "MTE3"
        if pipe == InstrQueueType.FIXP:
            return "FIXP"
        if pipe == InstrQueueType.SIMT:
            return "VEC"
        assert False

    @staticmethod
    def make_index() -> None:
        database_handler.execute("""
            CREATE INDEX IF NOT EXISTS idx_ei_dfx_core_subcore_tickend
            ON ExecutedInstructions(
                ExecInstrCoreId, CoreTypeId,
                ExecInstrName, ExecInstrTickEnd
            )
            WHERE ExecInstrName LIKE 'DFX_REGION%'
        """)

    @staticmethod
    def drop_index() -> None:
        database_handler.execute("""
        DROP INDEX idx_ei_dfx_core_subcore_tickend
        """)

    @staticmethod
    def get_dfx_instrs(core_id: int, core_type_id: int, pipe: str) -> List[sqlite3.Row]:
        dfx_instrs = database_handler.execute(
            """
            SELECT ExecInstrTickEnd, ExecInstrName, ExecInstrParams
            FROM ExecutedInstructions
            WHERE ExecInstrCoreId = :core_id AND
                CoreTypeId = :core_type_id AND
                json_extract(ExtendParams, '$.target_pipe') = :pipe AND
                ExecInstrName LIKE 'DFX_REGION%'
            ORDER BY ExecInstrTickEnd
            """,
            {"core_id": core_id, "core_type_id": core_type_id, "pipe": pipe},
        )
        result: List[sqlite3.Row] = dfx_instrs.fetchall()
        return result

    @staticmethod
    def get_start(value: int) -> bool:
        return (value >> 11) == 0

    # The 12th bit indicates whether this is the start or the end of a region.

    @staticmethod
    def get_custom(value: int) -> bool:
        return (value >> 10) & 1 == 0

    # The 11th bit indicates whether the region is custom or not.

    @staticmethod
    def get_id(value: int) -> int:
        return value & 0x3FF

    # The remaining 10 bits contain the region's ID.

    @staticmethod
    def get_full_id(value: int) -> int:
        return value & 0x7FF

    # 11-bit mask - ID plus the 'custom' indicator,
    # sufficient to identify a region.

    def process_regions(self, instrs: List[sqlite3.Row], core_id: int, core_type_id: int, pipe_id: int) -> None:
        stack: Dict[int, List[sqlite3.Row]] = {}
        for instr in instrs:
            params = instr["ExecInstrParams"]
            if params is None:
                continue
            time = instr["ExecInstrTickEnd"]

            res = self.pattern.search(params)
            if not res:
                log.warning("Unable to recognize DFX register from {}, skipped".format(params))
                continue
            value = int(res.group(1), 16)
            start = self.get_start(value)
            custom = self.get_custom(value)
            region_id = self.get_id(value)
            full_id = self.get_full_id(value)

            if start:
                if full_id not in stack:
                    stack[full_id] = []
                stack[full_id].append(instr)
            else:
                if full_id not in stack or len(stack[full_id]) == 0:
                    log.warning(f"Non-pair DFX_REGION found: {value:b}")
                    continue

                start_time = stack[full_id][-1]["ExecInstrTickEnd"]
                stack[full_id].pop()

                if start_time > time:
                    raise Exception("Region end time is less than start")

                if custom:
                    name = f"CustomRegion_{region_id}"
                else:
                    if region_id < 512:
                        name = f"ApiRegion_{region_id}"
                    else:
                        name = f"AutoRegion_{region_id - 512}"
                region = Region(
                    name=name,
                    start_time=start_time,
                    end_time=time,
                    core_id=core_id,
                    core_type_id=core_type_id,
                    region_type_id=InstrQueueType.SCALAR.value,
                    pipe_type_id=pipe_id,
                    warp_id=-1,
                )
                self.insert_region(region)

        for full_id in stack.keys():
            while len(stack[full_id]):
                stack[full_id].pop()
                log.warning(f"Non-pair DFX_REGION found: {full_id:b}")

    def process(self) -> None:
        unique_cores = self.get_unique_cores()
        self.make_index()
        for core in unique_cores:
            core_id = core["Id"]
            unique_core_types = self.get_unique_core_types(core_id)
            for core_type in unique_core_types:
                core_type_id = core_type["Id"]
                for pipe_id, pipe in enumerate(InstrQueueType):
                    if pipe == InstrQueueType.SIMT:
                        continue  # Treat SIMD & SIMT as the same VEC pipe
                    if pipe == InstrQueueType.NONE:
                        continue
                    dfx_instrs = self.get_dfx_instrs(core_id, core_type_id, self.get_pipe_template(pipe))
                    self.process_regions(dfx_instrs, core_id, core_type_id, pipe_id + 1)
        self.drop_index()
