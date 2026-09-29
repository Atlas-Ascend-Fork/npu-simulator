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

"""Recompute per-core instruction-pipe utilization from execution facts."""

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from npusim.prof_demo.database import database_handler, BackendDBManager


@dataclass(frozen=True)
class PipeInstruction:
    core_id: int
    core_type_id: int
    instr_type_id: int
    tick_start: int
    tick_end: int
    is_wait: bool
    name: str


# Queue IDs are the stable contract values from InstrQueueTypes.  SIMD and
# SIMT share the RVEC instruction-type range and are split by mnemonic.
_QUEUE_INSTR_TYPES = {
    1: (1, 55),
    2: (2,),
    3: (7,),
    4: (3,),
    5: (4,),
    6: (5,),
}


def _is_vector_type(instr_type_id: int) -> bool:
    return 50 <= instr_type_id <= 54


def matches_queue(instr: PipeInstruction, queue_type_id: int) -> bool:
    if instr.is_wait:
        return False

    is_simt_name = instr.name.upper().startswith("SIMT")
    if queue_type_id == 7:
        return _is_vector_type(instr.instr_type_id) and is_simt_name
    if queue_type_id == 8:
        return _is_vector_type(instr.instr_type_id) and not is_simt_name

    upper_name = instr.name.upper()
    if upper_name.startswith("VF") or upper_name.startswith("DFX_REGION"):
        return False
    return instr.instr_type_id in _QUEUE_INSTR_TYPES.get(queue_type_id, ())


def calculate_utilization(intervals: Iterable[PipeInstruction], kernel_exec_time_ticks: int) -> float:
    ordered = sorted(intervals, key=lambda instr: instr.tick_start)
    if not ordered or kernel_exec_time_ticks == 0:
        return 0.0

    segment_start = ordered[0].tick_start
    segment_end = ordered[0].tick_end
    gaps_total_time = 0.0
    for instr in ordered:
        if segment_start <= instr.tick_start <= segment_end:
            if instr.tick_end > segment_end:
                segment_end = instr.tick_end
        else:
            gaps_total_time += instr.tick_start - segment_end
            segment_start = instr.tick_start
            segment_end = instr.tick_end

    left_edge = ordered[0].tick_start
    right_edge = max(instr.tick_end for instr in ordered)
    busy_ticks = float(right_edge - left_edge) - gaps_total_time
    return busy_ticks / float(kernel_exec_time_ticks) * 100.0


class PipeUtilizationAnalyzer:
    @staticmethod
    def _load_instructions() -> List[PipeInstruction]:
        rows = database_handler.iter_rows("""
            SELECT ExecInstrCoreId, CoreTypeId, InstrTypeId,
                   ExecInstrTickStart, ExecInstrTickEnd, ExecInstrIsWait,
                   ExecInstrName
            FROM sim.ExecutedInstructions
        """)
        return [
            PipeInstruction(
                core_id=int(row["ExecInstrCoreId"]),
                core_type_id=int(row["CoreTypeId"]),
                instr_type_id=int(row["InstrTypeId"]),
                tick_start=int(row["ExecInstrTickStart"]),
                tick_end=int(row["ExecInstrTickEnd"]),
                is_wait=bool(row["ExecInstrIsWait"]),
                name=str(row["ExecInstrName"]),
            )
            for row in rows
        ]

    def process(self) -> None:
        instructions = self._load_instructions()
        # Match the helper's CORE_ENABLE_MASK selection through the report
        # command's public -n/--core-id setting.
        core_ids = sorted(BackendDBManager.get_active_cores())
        core_type_ids = sorted({instr.core_type_id for instr in instructions})
        exec_ticks = database_handler.get_exec_time_ticks()

        grouped: Dict[Tuple[int, int, int], List[PipeInstruction]] = {}
        for instr in instructions:
            for queue_type_id in range(1, 9):
                if matches_queue(instr, queue_type_id):
                    grouped.setdefault((instr.core_id, instr.core_type_id, queue_type_id), []).append(instr)

        output = []
        for core_id in core_ids:
            for core_type_id in core_type_ids:
                for queue_type_id in range(1, 9):
                    intervals = grouped.get((core_id, core_type_id, queue_type_id), [])
                    output.append(
                        (
                            core_id,
                            core_type_id,
                            queue_type_id,
                            calculate_utilization(intervals, exec_ticks),
                        )
                    )

        database_handler.execute("DELETE FROM main.PipesUtilization")
        database_handler.executemany(
            """
            INSERT INTO main.PipesUtilization(
                CoreId, CoreTypeId, InstrQueueTypeId, PipeUtilization)
            VALUES (?, ?, ?, ?)
        """,
            output,
        )
