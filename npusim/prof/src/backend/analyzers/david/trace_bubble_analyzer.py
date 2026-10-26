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

from typing import Dict, List, Optional

from npusim.prof.src.backend.analyzers import bubble_analysis_core as core
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import CoreType, InstrType
from npusim.prof.src.common.exceptions import RequirementsIncomplete
from npusim.prof.src.common.extend_params import ExtendParams

# InstrType -> bubble pipe bucket. The RVEC* types are the vector compute pipe
# (the main pipe on AIV cores). SCALAR is handled separately
# (see _resolve_pipe)
# because scalar load/store instructions form the virtual SCALARLDST pipe.
INSTRTYPE_TO_PIPE = {
    InstrType.RVECEX.value: core.PIPE_VECTOR,
    InstrType.RVECSU.value: core.PIPE_VECTOR,
    InstrType.RVECST.value: core.PIPE_VECTOR,
    InstrType.RVECLD.value: core.PIPE_VECTOR,
    InstrType.MTE1.value: core.PIPE_MTE1,
    InstrType.MTE2.value: core.PIPE_MTE2,
    InstrType.MTE3.value: core.PIPE_MTE3,
    InstrType.FIXP.value: core.PIPE_FIXPIPE,
    InstrType.FC.value: core.PIPE_FLOWCTRL,
    InstrType.CUBE.value: core.PIPE_CUBE,
}

# There is no separate SCALARLDST pipe: it is the load/store instructions on
# the SCALAR pipe, identified by ExecInstrName.lower() starting with ld/st.
SCALAR_LDST_PREFIXES = ("ld", "st")

# The PUSHQ pipe (queue push / VF macro instructions) is not analyzed at all;
# it is filtered out in get_instructions.
EXCLUDED_INSTR_TYPES = (InstrType.PUSHQ.value,)
_EXCLUDED_TYPES_SQL = ", ".join(str(v) for v in EXCLUDED_INSTR_TYPES)

# Pipe names inside ExtendParams -> pipe names the classifier understands.
PARAM_PIPE_TO_BUBBLE = {
    "VEC": core.PIPE_VECTOR,
    "CUBE": core.PIPE_CUBE,
    "FIXP": core.PIPE_FIXPIPE,
    "MTE1": core.PIPE_MTE1,
    "MTE2": core.PIPE_MTE2,
    "MTE3": core.PIPE_MTE3,
    "SCALAR": core.PIPE_SCALAR,
}

WAIT_FLAG_NAME = "WAIT_FLAG"


class DatabaseManager:
    @staticmethod
    def get_core_types(core_id: int) -> List[int]:
        rows = database_handler.execute(
            """
        SELECT DISTINCT CoreTypeId
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId = :core_id
        """,
            {"core_id": core_id},
        ).fetchall()
        return [row["CoreTypeId"] for row in rows]

    @staticmethod
    def get_instructions(core_id: int, core_type_id: int) -> List:
        return database_handler.execute(
            f"""
        SELECT ExecInstrName AS Name,
               ExtendParams AS Params,
               InstrTypeId AS TypeId,
               ExecInstrTickStart AS Start,
               ExecInstrTickEnd AS End
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId = :core_id AND CoreTypeId = :core_type_id
              AND InstrTypeId NOT IN ({_EXCLUDED_TYPES_SQL})
        ORDER BY ExecInstrTickStart
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchall()

    @staticmethod
    def get_core_duration(core_id: int, core_type_id: int) -> int:
        row = database_handler.execute(
            """
        SELECT MAX(ExecInstrTickEnd) - MIN(ExecInstrTickStart) AS Duration
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId = :core_id AND CoreTypeId = :core_type_id
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchone()
        if row is None or row["Duration"] is None:
            return 0
        return row["Duration"]

    @staticmethod
    def get_cachemiss_events(core_id: int, core_type_id: int) -> List:
        # ICacheEvents records both hits and misses at a single tick;
        # HitStatus = 0 (false) is a miss. (ICacheRefillEvents would only
        # show refills, not the misses themselves.)
        if not database_handler.table_exists("ICacheEvents"):
            return []
        return database_handler.execute(
            """
        SELECT EventTimeTick AS Tick
        FROM ICacheEvents
        WHERE CoreId = :core_id AND CoreTypeId = :core_type_id
              AND HitStatus = 0
        ORDER BY EventTimeTick
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchall()

    @staticmethod
    def save_bubbles(rows: List[tuple]) -> None:
        database_handler.executemany(
            """
        INSERT INTO TraceBubbles
        (CoreId, CoreTypeId, MainPipeline, SubType, Category,
         TickStart, TickEnd, GapTicks, GapUs, Reason, Optimizable,
         OptimizationHint, WaitedPipeline, IterationIndex)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            rows,
        )

    @staticmethod
    def save_summary(row: tuple) -> None:
        database_handler.execute(
            """
        INSERT INTO TraceBubbleSummary
        (CoreId, CoreTypeId, MainPipeline, TotalDurationUs,
         BubbleCount, TopBottleneck, SubtypeSummary)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            row,
        )


class Ascend950TraceBubbleAnalyzer(IAnalyzer):
    @staticmethod
    def get_name() -> str:
        return "ascend950_trace_bubble"

    def check_requirements(self) -> None:
        if not database_handler.count_rows("ExecutedInstructions"):
            raise RequirementsIncomplete("No executed instructions found")

    @staticmethod
    def _wait_flag_detail(extend_params_raw: Optional[str]) -> Dict[str, str]:
        """Build the classifier 'detail' for a WAIT_FLAG from ExtendParams.

        In the contract DB, the waited-on pipe is stored in the ExtendParams
        JSON as ``consumer_pipe`` (the pipe that must produce the flag) or
        ``target_pipe`` (fallback).  No regex parsing is needed.
        """
        ep = ExtendParams.parse(extend_params_raw)
        waited_pipe = ep.consumer_pipe or ep.target_pipe
        if waited_pipe is None:
            return {}
        bubble_pipe = PARAM_PIPE_TO_BUBBLE.get(waited_pipe)
        if bubble_pipe is None:
            return {}
        return {"detail": f"PIPE:{bubble_pipe}"}

    @classmethod
    def _resolve_pipe(cls, name: str, type_id: int) -> str:
        """Map a DB instruction to its bubble pipe bucket, or "" to skip.

        PUSHQ is already excluded by get_instructions; every other pipe is
        accounted for (RVEC* -> vector, the rest via INSTRTYPE_TO_PIPE).
        """
        if type_id == InstrType.SCALAR.value:
            # Scalar load/store go to the virtual SCALARLDST pipe.
            if name.lower().startswith(SCALAR_LDST_PREFIXES):
                return core.PIPE_SCALARLDST
            return core.PIPE_SCALAR
        return INSTRTYPE_TO_PIPE.get(type_id, "")

    @staticmethod
    def _event(name: str, start, end, args=None) -> core.TraceEvent:
        # ts/dur are kept in raw simulator ticks: that is the time scale the
        # bubble-size thresholds (1 / 30 / 500) were tuned for.
        return core.TraceEvent(name=name, ts=start, dur=max(1, end - start), args=args or {})

    def _build_pipelines(self, core_id: int, core_type_id: int):
        pipelines: Dict[str, List[core.TraceEvent]] = {}
        for instr in DatabaseManager.get_instructions(core_id, core_type_id):
            name = instr["Name"]
            pipe = self._resolve_pipe(name, instr["TypeId"])
            if not pipe:
                continue
            args = self._wait_flag_detail(instr["Params"]) if name == WAIT_FLAG_NAME else {}
            pipelines.setdefault(pipe, []).append(self._event(name, instr["Start"], instr["End"], args))

        for event in DatabaseManager.get_cachemiss_events(core_id, core_type_id):
            # Point-in-time miss; _event gives it a minimal 1-tick duration.
            tick = event["Tick"]
            pipelines.setdefault(core.PIPE_CACHEMISS, []).append(self._event("CACHEMISS", tick, tick))

        for events in pipelines.values():
            events.sort(key=lambda e: e.ts)
        return pipelines

    def _process_core(self, core_id: int, core_type_id: int) -> None:
        pipelines = self._build_pipelines(core_id, core_type_id)
        report = core.analyze_pipelines(
            pipelines,
            core_id=str(core_id),
            core_type=CoreType(core_type_id).name,
            total_duration=DatabaseManager.get_core_duration(core_id, core_type_id),
        )
        self._save_report(core_id, core_type_id, report)

    @staticmethod
    def _save_report(core_id: int, core_type_id: int, report: core.CoreReport) -> None:
        rows = [
            (
                core_id,
                core_type_id,
                report.main_pipeline,
                b.sub_type,
                b.category,
                b.tick_start,
                b.tick_end,
                b.gap,
                b.gap_us,
                b.reason,
                int(b.optimizable),
                b.optimization_hint,
                b.waited_pipeline,
                b.iteration_index,
            )
            for b in report.bubbles
        ]
        if rows:
            DatabaseManager.save_bubbles(rows)

        subtype_summary = ", ".join(
            f"{name}:{count}" for name, count in sorted(report.subtype_summary.items(), key=lambda x: -x[1])
        )
        DatabaseManager.save_summary(
            (
                core_id,
                core_type_id,
                report.main_pipeline,
                report.total_duration_us,
                report.bubble_count,
                report.top_bottleneck,
                subtype_summary,
            )
        )

    def process(self) -> None:
        for core_id in self.get_active_cores():
            for core_type_id in DatabaseManager.get_core_types(core_id):
                self._process_core(core_id, core_type_id)
