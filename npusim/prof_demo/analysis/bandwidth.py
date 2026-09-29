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

"""Recompute v100 summary bandwidth metrics from contract-v5 facts (consumed directly by summary)."""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from npusim.prof_demo.database import database_handler


BYTES_IN_GB = 1024.0 * 1024.0 * 1024.0

OUT_TO_L1_INSTRS = (
    "MOV_SRC_TO_DST_ALIGNv2",
    "MOV_OUT_TO_L1_MULTI_ND2NZ",
    "MOV_OUT_TO_L1_MULTI_DN2NZ",
)
L1_TO_L0_INSTRS = (
    "LOAD_2Dv2",
    "LOAD_L1_TO_DST_3DV2",
    "LOAD_MX_2Dv2",
)
OUT_TO_UB_INSTRS = ("MOV_SRC_TO_DST_ALIGNv2", "ND_DMA")
UB_TO_L1_INSTRS = ("MOV_UB_TO_L1",)


@dataclass(frozen=True)
class TimedBytes:
    processed_bytes: float
    tick_start: int
    tick_end: int


@dataclass(frozen=True)
class ThroughputStats:
    total_bytes: float = 0.0
    count: int = 0
    average_bytes_per_tick_per_instruction: float = 0.0


def calculate_throughput_stats(rows: Iterable[TimedBytes]) -> ThroughputStats:
    total_bytes = 0.0
    count = 0
    current_left = 0
    current_right = 0
    total_exec_time = 0
    for row in rows:
        total_bytes += row.processed_bytes
        count += 1
        if row.tick_start >= current_right:
            total_exec_time += current_right - current_left
            current_left = row.tick_start
            current_right = row.tick_end
        else:
            current_left = min(current_left, row.tick_start)
            current_right = max(current_right, row.tick_end)
    total_exec_time += current_right - current_left
    average = 0.0
    if count != 0 and total_exec_time != 0:
        average = total_bytes / float(total_exec_time) / float(count)
    return ThroughputStats(total_bytes, count, average)


class BandwidthAnalyzer:
    @staticmethod
    def _load_timed(query: str, params: Sequence = ()) -> List[TimedBytes]:
        return [
            TimedBytes(
                float(row["ProcessedBytes"]),
                int(row["TickStart"]),
                int(row["TickEnd"]),
            )
            for row in database_handler.iter_rows(query, params)
            if row["ProcessedBytes"] is not None
        ]

    def _dma_stats(
        self, instr_names: Tuple[str, ...], src_space: str, dst_space: str, core_type_id: int
    ) -> ThroughputStats:
        placeholders = ",".join("?" for _ in instr_names)
        query = f"""
            SELECT PB.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM main.DmaMovProcessedBytes AS PB
            JOIN sim.ExecutedInstructions AS EI
              ON EI.ExecInstrId=PB.ExecInstrId
            JOIN main.ExtendParamsProjection AS EP
              ON EP.ExecInstrId=EI.ExecInstrId
            WHERE EI.ExecInstrName IN ({placeholders})
              AND EP.src_mem=? AND EP.dst_mem=? AND EI.CoreTypeId=?
            ORDER BY EI.ExecInstrTickEnd
        """
        params = (*instr_names, src_space, dst_space, core_type_id)
        return calculate_throughput_stats(self._load_timed(query, params))

    def _fixp_stats(self, dst_space: str) -> ThroughputStats:
        query = """
            SELECT EP.processed_bytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM sim.ExecutedInstructions AS EI
            JOIN main.ExtendParamsProjection AS EP
              ON EP.ExecInstrId=EI.ExecInstrId
            WHERE EI.ExecInstrName='FIX_L0C_TO_DST'
              AND EP.src_mem='L0C' AND EP.dst_mem=?
            ORDER BY EI.ExecInstrTickEnd
        """
        return calculate_throughput_stats(self._load_timed(query, (dst_space,)))

    def _fixp_ub_stats(self, ub_mask: int) -> ThroughputStats:
        rows = database_handler.iter_rows(
            """
            SELECT EP.processed_bytes AS ProcessedBytes,
                   EP.dst_ub_mask AS DstUBMask,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM sim.ExecutedInstructions AS EI
            JOIN main.ExtendParamsProjection AS EP
              ON EP.ExecInstrId=EI.ExecInstrId
            WHERE EI.ExecInstrName='FIX_L0C_TO_DST'
              AND EP.src_mem='L0C' AND EP.dst_mem='UB'
              AND EP.dst_ub_mask IN (?, 3)
            ORDER BY EI.ExecInstrTickEnd
        """,
            (ub_mask,),
        )
        timed = []
        for row in rows:
            processed_bytes = float(row["ProcessedBytes"])
            if int(row["DstUBMask"]) == 3:
                processed_bytes /= 2.0
            timed.append(
                TimedBytes(
                    processed_bytes,
                    int(row["TickStart"]),
                    int(row["TickEnd"]),
                )
            )
        return calculate_throughput_stats(timed)

    def compute(self) -> Tuple[Dict[str, float], Dict[str, float]]:
        """Return (metrics, utilization); metrics keys are <name>_gbs/_cnt/_ratio."""
        raw = {
            "aic_out_to_l1": self._dma_stats(OUT_TO_L1_INSTRS, "OUT", "L1", 1),
            "aic_l1_to_l0a": self._dma_stats(L1_TO_L0_INSTRS, "L1", "L0A", 1),
            "aic_l1_to_l0b": self._dma_stats(L1_TO_L0_INSTRS, "L1", "L0B", 1),
            "aic_l0c_to_out": self._fixp_stats("OUT"),
            "aic_l0c_to_l1": self._fixp_stats("L1"),
            "aic_l0c_to_ub0": self._fixp_ub_stats(1),
            "aic_l0c_to_ub1": self._fixp_ub_stats(2),
        }
        for prefix, core_type_id in (("aiv0", 2), ("aiv1", 3)):
            raw[f"{prefix}_out_to_ub"] = self._dma_stats(OUT_TO_UB_INSTRS, "OUT", "UB", core_type_id)
            raw[f"{prefix}_ub_to_out"] = self._dma_stats(OUT_TO_UB_INSTRS, "UB", "OUT", core_type_id)
            raw[f"{prefix}_ub_to_l1"] = self._dma_stats(UB_TO_L1_INSTRS, "UB", "L1", core_type_id)
        active_cores = database_handler.execute(
            "SELECT COUNT(DISTINCT ExecInstrCoreId) FROM sim.ExecutedInstructions"
        ).fetchone()[0]
        frequency_hz = float(database_handler.get_frequency_ghz()) * 1.0e9
        exec_seconds = float(database_handler.get_exec_time_ticks()) / frequency_hz
        timing = database_handler.execute(
            "SELECT MIN(ExecInstrTickStart), MAX(ExecInstrTickEnd) FROM sim.ExecutedInstructions"
        ).fetchone()
        span_seconds = float(timing[1] - timing[0]) / frequency_hz
        metrics: Dict[str, float] = {}
        utilization: Dict[str, float] = {}
        for name, stats in raw.items():
            gbs = stats.total_bytes / float(active_cores) / exec_seconds / BYTES_IN_GB
            ratio = stats.average_bytes_per_tick_per_instruction / float(active_cores) / BYTES_IN_GB * frequency_hz
            metrics.update({name + "_gbs": gbs, name + "_cnt": float(stats.count), name + "_ratio": ratio})
            peak = 393.39 if name in ("aic_l1_to_l0a", "aic_l1_to_l0b") else 196.7
            denominator = float(active_cores) * span_seconds * BYTES_IN_GB
            utilization[name] = stats.total_bytes / denominator / peak
        return metrics, utilization
