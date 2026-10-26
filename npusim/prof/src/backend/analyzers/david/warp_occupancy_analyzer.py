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
from typing import List, Any, Dict, Tuple, Optional, Iterator
from collections import defaultdict
import re
import sqlite3

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.biprof_constants import MAX_SIMT_WARPS


@dataclass
class WarpOccupancyMetric:
    vf_simt_id: int
    warp_occupancy: float


@dataclass
class WarpUtilizationMetric:
    simt_block_id: int
    warp_id: int
    tick_start: int
    dur: int
    warp_utilization: float
    core_id: int


class DatabaseManager:
    @staticmethod
    def iter_instrs(core_id: int) -> Iterator[sqlite3.Row]:
        return database_handler.iter_rows(
            """
        SELECT ExecInstrId, ExecInstrName,
               ExecInstrParams, ExecInstrTickStart, ExecInstrTickEnd,
               ExecInstrCoreId
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId=:core_id AND
              ExecInstrName LIKE '%SIMT%'
        ORDER BY ExecInstrTickStart
        """,
            {"core_id": core_id},
        )

    @staticmethod
    def get_exec_mask_bin(instr_id: int) -> str:
        mask = database_handler.execute(
            """
        SELECT ExecutionMask
        FROM SIMTExecutionMasks
        WHERE ExecInstrId=:instr_id
        """,
            {"instr_id": instr_id},
        ).fetchone()["ExecutionMask"]
        return bin(mask)

    @staticmethod
    def insert_kernel_metrics(
        warp_occupancy_metrics: List[WarpOccupancyMetric],
        warp_utilization_metrics: List[WarpUtilizationMetric],
    ) -> None:
        database_handler.executemany(
            """
        INSERT INTO
            SIMTWarpOccupancy (
                ExecInstrId,
                WarpOccupancy
            )
        VALUES
        (?, ?)
        """,
            [astuple(x) for x in warp_occupancy_metrics],
        )

        database_handler.executemany(
            """
        INSERT INTO
            SIMTWarpUtilization (
                SIMTBlockId,
                WarpId,
                TickStart,
                Duration,
                WarpUtilization,
                CoreId
            )
        VALUES
        (?, ?, ?, ?, ?, ?)
        """,
            [astuple(x) for x in warp_utilization_metrics],
        )


class Ascend950WarpOccupancyAnalyzer(IAnalyzer):
    # Warp occupancy = fraction of available SIMT warps that are active
    # within a VF_SIMT block. For each block, we:
    # 1. Group inner SIMT instructions by warp_id (extracted from params).
    # 2. Per warp: compute utilization from execution mask (set bits / 32).
    # 3. Occupancy = min(sum(warp_durations) / block_duration / MAX_WARPS, 1.0)
    WARP_ID_PAT = re.compile(r"\[warpId:\s*(?P<warp_id>[0-9]+)\]")

    def __init__(self) -> None:
        super().__init__()
        self._warp_occupancy_metrics: List[WarpOccupancyMetric] = []
        self._warp_utilization_metrics: List[WarpUtilizationMetric] = []

    @staticmethod
    def get_name() -> str:
        return "ascend950_warp_occupancy"

    def get_warp_instrs(self, simt_instrs: List[sqlite3.Row]) -> Dict[int, List[sqlite3.Row]]:
        res = defaultdict(list)
        for instr in simt_instrs:
            params = instr["ExecInstrParams"]
            if params is None:
                continue
            match = self.WARP_ID_PAT.search(params)
            # Some instructions may not have warpId now
            if not match:
                continue
            warp_id = int(match["warp_id"])
            res[warp_id].append(instr)
        return res

    @staticmethod
    def compute_block_duration(simt_instrs: List[sqlite3.Row]) -> Any:
        assert len(simt_instrs) != 0
        start_time = simt_instrs[0]["ExecInstrTickStart"]  # sorted
        end_time = max(simt_instrs, key=lambda x: x["ExecInstrTickEnd"])["ExecInstrTickEnd"]
        return end_time - start_time

    def compute_warp_metrics(
        self,
        warp_instrs: Dict[int, List[sqlite3.Row]],
        ids: Tuple[int, int, int],
        total_block_duration: int,
    ):
        vf_simt_id, block_id, core_id = ids
        sum = 0.0
        for warp_id, instrs in warp_instrs.items():
            assert len(instrs) != 0
            instr_id = warp_instrs[warp_id][0]["ExecInstrId"]
            exec_mask_bin = DatabaseManager.get_exec_mask_bin(instr_id)
            warp_utilization = exec_mask_bin.count("1") / 32
            warp_start = instrs[0]["ExecInstrTickStart"]  # sorted
            warp_end = max(instrs, key=lambda x: int(x["ExecInstrTickEnd"]))["ExecInstrTickEnd"]
            warp_dur = warp_end - warp_start
            self._warp_utilization_metrics.append(
                WarpUtilizationMetric(block_id, warp_id, warp_start, warp_dur, warp_utilization, core_id)
            )
            sum += warp_dur
        warp_occupancy = min(sum / total_block_duration / MAX_SIMT_WARPS, 1.0)
        self._warp_occupancy_metrics.append(WarpOccupancyMetric(vf_simt_id, warp_occupancy))

    def process_single_block(
        self,
        vf_simt: sqlite3.Row,
        simt_block: List[sqlite3.Row],
        block_id: int,
    ) -> None:
        vf_simt_id = vf_simt["ExecInstrId"]
        core_id = vf_simt["ExecInstrCoreId"]
        warp_instrs = self.get_warp_instrs(simt_block)

        total_block_duration = self.compute_block_duration(simt_block)
        self.compute_warp_metrics(warp_instrs, (vf_simt_id, block_id, core_id), total_block_duration)

    def process(self) -> None:
        active_cores = IAnalyzer.get_active_cores()

        block_id: int = -1
        for core_id in active_cores:
            vf_simt: Optional[sqlite3.Row] = None
            simt_block: List[sqlite3.Row] = []

            for instr in DatabaseManager.iter_instrs(core_id):
                # Processing VF_SIMT instr
                if instr["ExecInstrName"] == "VF_SIMT":
                    if vf_simt:
                        block_id += 1
                        self.process_single_block(vf_simt, simt_block, block_id)
                        simt_block.clear()
                    vf_simt = instr
                    continue
                # Processing VF_SIMT inner instrs
                simt_block.append(instr)

            # Processing VF_SIMT inner instrs end
            if simt_block and vf_simt:
                block_id += 1
                self.process_single_block(vf_simt, simt_block, block_id)
                simt_block.clear()

        DatabaseManager.insert_kernel_metrics(self._warp_occupancy_metrics, self._warp_utilization_metrics)

    def cleanup(self) -> None:
        self._warp_occupancy_metrics.clear()
        self._warp_utilization_metrics.clear()
