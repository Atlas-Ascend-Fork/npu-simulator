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

from dataclasses import dataclass, astuple
from typing import List, Dict
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.memory_analyzer_base import (
    MemoryAnalyzerBase,
)
import npusim.prof.src.common.biprof_constants as constants


@dataclass
class MemoryAccessMetric:
    instr_id: int
    cache_line_count: int
    cache_line_utilization: float
    memory_access_stride: float


class Ascend950SIMTMemoryAccessAnalyzer(MemoryAnalyzerBase):
    # Analyzes SIMT global memory access patterns for coalescing efficiency.
    # For each SIMT load instruction, computes:
    #  1. Cache line count: how many 128-byte cache lines are touched.
    #  2. Cache line utilization: % of each cache line actually used.
    #  3. Average stride: distance between consecutive addresses (in elements).
    # Well-coalesced accesses hit few cache lines with high utilization.
    def __init__(self) -> None:
        super().__init__()
        self._mem_access_metrics: List[MemoryAccessMetric] = []

    @staticmethod
    def get_name() -> str:
        return "ascend950_simt_memory_access"

    @staticmethod
    def get_read_addresses() -> Dict[int, List[int]]:
        cur = database_handler.execute("""
        SELECT ProcessedAddress, ExecInstrId, Mask, ThreadId
        FROM SIMTGMPatterns
        ORDER BY ExecInstrId, ThreadId, RegIndex
        """).fetchall()
        res: Dict[int, List[int]] = defaultdict(list)
        for id in range(0, len(cur), 2):
            item_reg0 = cur[id]
            item_reg1 = cur[id + 1]
            if not (item_reg0["Mask"] & (1 << item_reg0["ThreadId"])):
                continue
            addr = item_reg1["ProcessedAddress"] << 32 | item_reg0["ProcessedAddress"]
            res[item_reg0["ExecInstrId"]].append(addr)
        return res

    @classmethod
    def get_instr_dtype(cls, id: int) -> str:
        instr = database_handler.execute(
            """
        SELECT ExtendParams, ExecInstrName
        FROM ExecutedInstructions
        WHERE ExecInstrId=?
        """,
            (id,),
        ).fetchone()
        return cls.match_type(instr)

    @classmethod
    def get_mem_access_metrics(cls, id: int, addrs: List[int]):
        dtype = cls.get_instr_dtype(id)
        dtype_size = cls.get_dtype_size(dtype)
        unique_addrs = set(addrs)
        cache_line_size = constants.DAVID_CACHE_LINE_SIZE
        cache_line_nums = [addr // cache_line_size for addr in unique_addrs]
        cache_lines_set = set(cache_line_nums)
        cache_lines_num = len(cache_lines_set)
        cache_lines_utilization = []
        for i in cache_lines_set:
            used_bytes = cache_line_nums.count(i) * dtype_size
            cache_lines_utilization.append(used_bytes / cache_line_size * 100)
        avg_cache_lines_utilization = round(sum(cache_lines_utilization) / cache_lines_num, 2)
        strides = []
        for i in range(1, len(addrs)):
            stride = (addrs[i] - addrs[i - 1]) / dtype_size
            strides.append(stride)
        avg_stride = 0.0
        if len(strides) != 0:
            avg_stride = sum(strides) / len(strides)
        return MemoryAccessMetric(id, cache_lines_num, avg_cache_lines_utilization, avg_stride)

    def process(self) -> None:
        read_addresses = self.get_read_addresses()
        for id, addrs in read_addresses.items():
            addrs.sort()
            metric = self.get_mem_access_metrics(id, addrs)
            self._mem_access_metrics.append(metric)
        self.insert_kernel_metrics()

    def cleanup(self) -> None:
        self._mem_access_metrics.clear()

    def insert_kernel_metrics(self) -> None:
        database_handler.executemany(
            """
        INSERT INTO
            SIMTGmAccessMetrics (
                ExecInstrId,
                CacheLineCount,
                CacheLineUtilization,
                MemAccessStride
            )
        VALUES
        (?, ?, ?, ?)
        """,
            [astuple(x) for x in self._mem_access_metrics],
        )
