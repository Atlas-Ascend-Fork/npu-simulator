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

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from npusim.prof.src.common.biprof_constants import frequency_ghz_map
from npusim.prof.src.common.biprof_context import biprof_ctx

# -- Pipeline identities (plain names, no PIDs) -----------------------------

PIPE_SCALAR = "SCALAR"
PIPE_SCALARLDST = "SCALARLDST"
PIPE_VECTOR = "VECTOR"
PIPE_CUBE = "CUBE"
PIPE_MTE1 = "MTE1"
PIPE_MTE2 = "MTE2"
PIPE_MTE3 = "MTE3"
PIPE_FIXPIPE = "FIXPIPE"
PIPE_FLOWCTRL = "FLOWCTRL"
PIPE_CACHEMISS = "CACHEMISS"

DRAIN_OPS = frozenset(
    {
        "VNCHWCONV",
        "BAR",
        "PipeBarrier",
        "PIPE_BARRIER",
        "VNCHWCONV_B16",
        "VNCHWCONV_B32",
    }
)
BARRIER_OPS = frozenset({"BAR", "PipeBarrier", "PIPE_BARRIER"})

# Gap-size tiers. The unit is whatever time unit the caller uses for
# TraceEvent.ts / .dur; the database analyzer feeds raw simulator ticks, which
# is the scale these thresholds were tuned for.
THRESHOLD_A_MAX = 1.0
THRESHOLD_B_DRAIN = 30.0
THRESHOLD_C_MAX = 500.0


@dataclass
class TraceEvent:
    name: str
    ts: int
    dur: int
    args: dict = field(default_factory=dict)

    @property
    def end_ts(self) -> int:
        return self.ts + self.dur

    def is_wait_flag(self) -> bool:
        return self.name == "WAIT_FLAG"

    def waited_pipeline(self) -> Optional[str]:
        if not self.is_wait_flag():
            return None
        detail = self.args.get("detail", "")
        m = re.search(r"PIPE:(\w+)", detail)
        return m.group(1) if m else None

    def is_drain_op(self) -> bool:
        return self.name in DRAIN_OPS


# A pipelines structure is just {pipe_name: [TraceEvent, ...]} sorted by ts.
Pipelines = Dict[str, List[TraceEvent]]


class BubbleCategory(Enum):
    NORMAL = "normal"
    STRUCTURAL = "structural"
    DATA_STALL = "data_stall"
    SCALAR_OVERHEAD = "scalar_overhead"
    RESOURCE_CONTENTION = "resource_contention"
    CROSS_CORE = "cross_core"
    CUBE_VECTOR = "cube_vector"


@dataclass
class ConcurrentState:
    scalar_coverage: float = 0.0
    scalarldst_coverage: float = 0.0
    mte2_coverage: float = 0.0
    mte3_coverage: float = 0.0
    mte1_coverage: float = 0.0
    cube_coverage: float = 0.0
    fixpipe_coverage: float = 0.0
    cachemiss_active: bool = False
    flowctrl_active: bool = False

    @property
    def scalar_busy(self) -> bool:
        return self.scalar_coverage > 0.05

    @property
    def scalarldst_busy(self) -> bool:
        return self.scalarldst_coverage > 0.05

    @property
    def mte2_busy(self) -> bool:
        return self.mte2_coverage > 0.05

    @property
    def mte3_busy(self) -> bool:
        return self.mte3_coverage > 0.05

    @property
    def mte1_busy(self) -> bool:
        return self.mte1_coverage > 0.05

    @property
    def cube_busy(self) -> bool:
        return self.cube_coverage > 0.05

    @property
    def fixpipe_busy(self) -> bool:
        return self.fixpipe_coverage > 0.05

    @property
    def all_idle(self) -> bool:
        return not (
            self.scalar_busy
            or self.scalarldst_busy
            or self.mte2_busy
            or self.mte3_busy
            or self.mte1_busy
            or self.cube_busy
            or self.fixpipe_busy
        )


@dataclass
class BubbleResult:
    sub_type: str
    category: str
    gap: int
    gap_us: float
    reason: str
    optimizable: bool
    optimization_hint: Optional[str] = None
    waited_pipeline: Optional[str] = None
    concurrent_cause: Optional[str] = None
    iteration_index: Optional[int] = None
    tick_start: Optional[int] = None
    tick_end: Optional[int] = None


@dataclass
class CoreReport:
    core_id: str
    core_type: str
    total_duration_us: float
    main_pipeline: str
    bubble_count: int
    bubbles: list = field(default_factory=list)
    subtype_summary: dict = field(default_factory=dict)
    top_bottleneck: Optional[str] = None


@dataclass
class GapContext:
    gap: int
    op_before: Optional[TraceEvent]
    op_after: Optional[TraceEvent]
    cs: ConcurrentState
    has_wait_flag: bool = False
    wait_flag_event: Optional[TraceEvent] = None
    iteration_index: Optional[int] = None
    total_iterations: Optional[int] = None


# -- Windowed coverage sweep ------------------------------------------------


class _PipeSweep:
    """Forward-only cursor over one pipe's ts-sorted events.

    Bubbles are the gaps between main-pipe events, so the query windows arrive
    with non-decreasing start times. That lets a single cursor sweep each pipe
    once across all windows (amortized O(events + windows)) instead of
    rescanning every pipe from scratch for every gap
    (the old O(gaps x events)).
    """

    def __init__(self, events: List[TraceEvent]):
        self._events = events
        self._start = 0

    def _advance(self, window_start: int) -> None:
        # Events ending at/before window_start cannot overlap this window nor
        # any later one (their starts are >= window_start), so drop them.
        events = self._events
        while self._start < len(events) and events[self._start].end_ts <= window_start:
            self._start += 1

    def coverage(self, ws: int, we: int) -> float:
        if we <= ws:
            return 0.0
        self._advance(ws)
        events, n = self._events, len(self._events)
        i = self._start
        covered = 0.0
        while i < n and events[i].ts < we:
            e = events[i]
            i += 1
            if e.end_ts <= ws:
                continue
            covered += min(e.end_ts, we) - max(e.ts, ws)
        return min(covered / (we - ws), 1.0)

    def first_event(self, ws: int, we: int) -> Optional[TraceEvent]:
        self._advance(ws)
        events, n = self._events, len(self._events)
        i = self._start
        while i < n and events[i].ts < we:
            if events[i].end_ts > ws:
                return events[i]
            i += 1
        return None

    def has_event(self, ws: int, we: int) -> bool:
        return self.first_event(ws, we) is not None


def _make_sweeps(pipelines: Pipelines, exclude_pipe: str) -> Dict[str, _PipeSweep]:
    return {pipe: _PipeSweep(events) for pipe, events in pipelines.items() if pipe != exclude_pipe}


def build_concurrent_state(
    sweeps: Dict[str, _PipeSweep], window_start: int, window_end: int, core_type: str
) -> ConcurrentState:
    def cov(pipe: str) -> float:
        sweep = sweeps.get(pipe)
        return sweep.coverage(window_start, window_end) if sweep else 0.0

    def active(pipe: str) -> bool:
        sweep = sweeps.get(pipe)
        return sweep.has_event(window_start, window_end) if sweep else False

    state = ConcurrentState()
    state.scalar_coverage = cov(PIPE_SCALAR)
    state.scalarldst_coverage = cov(PIPE_SCALARLDST)
    state.mte2_coverage = cov(PIPE_MTE2)
    state.cachemiss_active = active(PIPE_CACHEMISS)
    state.flowctrl_active = active(PIPE_FLOWCTRL)

    if core_type == "veccore":
        state.mte3_coverage = cov(PIPE_MTE3)
    elif core_type == "cubecore":
        state.mte1_coverage = cov(PIPE_MTE1)
        state.cube_coverage = cov(PIPE_CUBE)
        state.fixpipe_coverage = cov(PIPE_FIXPIPE)

    return state


# -- Classification helpers -------------------------------------------------

# Reused optimization hints.
HINT_DOUBLE_BUFFER = "Enable double buffering to overlap {0} with {1}"
HINT_SCALAR = "Reduce scalar dependency: simplify control flow or precompute"

# Coverage threshold above which a supporting pipe is considered the cause of a
# gap: 30% in the mid-range tier, 50% in the large-gap (implicit) tier.
COVERAGE_MID = 0.3
COVERAGE_LARGE = 0.5


def _make(
    ctx: GapContext,
    sub_type: str,
    category: BubbleCategory,
    reason: str,
    optimizable: bool = False,
    hint: Optional[str] = None,
    waited: Optional[str] = None,
) -> BubbleResult:
    """Build a BubbleResult, filling the gap fields from ``ctx``."""
    freq_ghz = frequency_ghz_map[biprof_ctx.arch]
    ticks_to_ns_coeff = 1 / (freq_ghz * 1000)
    return BubbleResult(
        sub_type,
        category.value,
        ctx.gap,
        ctx.gap * ticks_to_ns_coeff,
        reason,
        optimizable,
        hint,
        waited,
    )


def _check_threshold_a(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.gap > THRESHOLD_A_MAX:
        return None
    return _make(ctx, "n_issue_gap", BubbleCategory.NORMAL, "Normal issue gap")


def _check_threshold_b(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.gap > THRESHOLD_B_DRAIN:
        return None
    before = ctx.op_before
    if before and before.is_drain_op():
        if before.name in BARRIER_OPS:
            return _make(
                ctx,
                "s_barrier",
                BubbleCategory.STRUCTURAL,
                f"PipeBarrier sync after {before.name}",
            )
        return _make(
            ctx,
            "s_drain",
            BubbleCategory.STRUCTURAL,
            f"Pipeline drain after {before.name}",
        )
    return _make(ctx, "n_issue_gap", BubbleCategory.NORMAL, "Small gap, non-drain")


def _check_threshold_c_common(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.gap > THRESHOLD_C_MAX:
        return None
    cs = ctx.cs
    if cs.cachemiss_active:
        return _make(
            ctx,
            "s_icache_miss",
            BubbleCategory.STRUCTURAL,
            "Instruction cache miss detected",
        )
    if cs.flowctrl_active:
        return _make(
            ctx,
            "s_flowctrl",
            BubbleCategory.STRUCTURAL,
            "Flow control instruction overhead",
        )
    if cs.scalarldst_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "sc_ldst_block",
            BubbleCategory.SCALAR_OVERHEAD,
            f"SCALARLDST busy (coverage={cs.scalarldst_coverage:.0%})",
            True,
            "Parameter prefetch: preload next tile params during compute",
            "SCALARLDST",
        )
    if cs.scalar_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "sc_compute_block",
            BubbleCategory.SCALAR_OVERHEAD,
            f"SCALAR busy (coverage={cs.scalar_coverage:.0%})",
            True,
            "Reduce address computation: simplify tiling or precompute offsets",
            "SCALAR",
        )
    if cs.all_idle:
        return _make(
            ctx,
            "s_drain",
            BubbleCategory.STRUCTURAL,
            "Extended drain / all pipelines idle",
        )
    return None


def _check_threshold_c_vec(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.gap > THRESHOLD_C_MAX:
        return None
    cs = ctx.cs
    if cs.mte2_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"MTE2 busy in mid-range gap (coverage={cs.mte2_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE2", "VEC"),
            "MTE2",
        )
    if cs.mte3_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "d_mte3_implicit",
            BubbleCategory.DATA_STALL,
            f"MTE3 busy in mid-range gap (coverage={cs.mte3_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE3", "VEC"),
            "MTE3",
        )
    return _make(ctx, "s_drain", BubbleCategory.STRUCTURAL, "Mid-range gap, non-scalar cause")


def _check_threshold_c_cube(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.gap > THRESHOLD_C_MAX:
        return None
    cs = ctx.cs
    if cs.fixpipe_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "cv_handoff",
            BubbleCategory.CUBE_VECTOR,
            f"FIXPIPE busy (coverage={cs.fixpipe_coverage:.0%}), L0C->UB conversion",
            waited="FIXPIPE",
        )
    if cs.mte1_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"MTE1 busy (L1->L0 load, coverage={cs.mte1_coverage:.0%})",
            True,
            "Overlap MTE1 data load with CUBE compute via double buffering",
            "MTE1",
        )
    if cs.mte2_coverage > COVERAGE_MID:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"MTE2 busy (GM->L1 load, coverage={cs.mte2_coverage:.0%})",
            True,
            "Enable double buffering to overlap MTE2 GM->L1 with compute",
            "MTE2",
        )
    return _make(ctx, "s_drain", BubbleCategory.STRUCTURAL, "Mid-range gap, non-scalar cause")


def _check_iteration_boundary(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.iteration_index is None or ctx.total_iterations is None:
        return None
    if ctx.total_iterations <= 1:
        return None
    if ctx.iteration_index == 0:
        return _make(
            ctx,
            "s_cold_start",
            BubbleCategory.STRUCTURAL,
            "First tile pipeline fill (cold start)",
        )
    if ctx.iteration_index == ctx.total_iterations - 1:
        return _make(
            ctx,
            "s_tail_drain",
            BubbleCategory.STRUCTURAL,
            "Last tile pipeline drain (tail)",
        )
    return None


def _check_wait_flag_vec(ctx: GapContext) -> Optional[BubbleResult]:
    if not ctx.has_wait_flag or not ctx.wait_flag_event:
        return None
    waited = ctx.wait_flag_event.waited_pipeline()
    cs = ctx.cs
    if waited == "MTE2" or (waited is None and cs.mte2_busy):
        return _make(
            ctx,
            "d_mte2_wait",
            BubbleCategory.DATA_STALL,
            "WAIT_FLAG for MTE2 (data load stall)",
            True,
            HINT_DOUBLE_BUFFER.format("MTE2", "VEC"),
            "MTE2",
        )
    if waited == "MTE3" or (waited is None and cs.mte3_busy):
        return _make(
            ctx,
            "d_mte3_wait",
            BubbleCategory.DATA_STALL,
            "WAIT_FLAG for MTE3 (write-back stall)",
            True,
            HINT_DOUBLE_BUFFER.format("MTE3", "VEC"),
            "MTE3",
        )
    if waited == "SCALAR":
        return _make(
            ctx,
            "sc_compute_block",
            BubbleCategory.SCALAR_OVERHEAD,
            "WAIT_FLAG for SCALAR",
            True,
            HINT_SCALAR,
            "SCALAR",
        )
    return _make(
        ctx,
        "d_mte2_wait",
        BubbleCategory.DATA_STALL,
        f"WAIT_FLAG for {waited or 'unknown'}",
        True,
    )


def _check_wait_flag_cube(ctx: GapContext) -> Optional[BubbleResult]:
    if not ctx.has_wait_flag or not ctx.wait_flag_event:
        return None
    waited = ctx.wait_flag_event.waited_pipeline()
    cs = ctx.cs
    if waited == "MTE1" or (waited is None and cs.mte1_busy):
        return _make(
            ctx,
            "d_mte2_wait",
            BubbleCategory.DATA_STALL,
            "WAIT_FLAG for MTE1 (L1->L0 data load stall)",
            True,
            HINT_DOUBLE_BUFFER.format("MTE1", "CUBE"),
            "MTE1",
        )
    if waited == "MTE2" or (waited is None and cs.mte2_busy):
        return _make(
            ctx,
            "d_mte2_wait",
            BubbleCategory.DATA_STALL,
            "WAIT_FLAG for MTE2 (GM->L1 data load stall)",
            True,
            HINT_DOUBLE_BUFFER.format("MTE2", "CUBE"),
            "MTE2",
        )
    if waited == "FIXPIPE" or (waited is None and cs.fixpipe_busy):
        return _make(
            ctx,
            "cv_handoff",
            BubbleCategory.CUBE_VECTOR,
            "WAIT_FLAG for FIXPIPE (L0C->UB conversion)",
            waited="FIXPIPE",
        )
    if waited == "SCALAR":
        return _make(
            ctx,
            "sc_compute_block",
            BubbleCategory.SCALAR_OVERHEAD,
            "WAIT_FLAG for SCALAR",
            True,
            HINT_SCALAR,
            "SCALAR",
        )
    return _make(
        ctx,
        "d_mte2_wait",
        BubbleCategory.DATA_STALL,
        f"WAIT_FLAG for {waited or 'unknown'}",
        True,
    )


def _check_wait_flag_unavailable(ctx: GapContext) -> Optional[BubbleResult]:
    if not ctx.has_wait_flag:
        return None
    return _make(
        ctx,
        "d_mte2_wait",
        BubbleCategory.DATA_STALL,
        "WAIT_FLAG (detail unavailable)",
        True,
        "Enable double buffering to overlap data transfer with compute",
    )


def _check_implicit_vec(ctx: GapContext) -> Optional[BubbleResult]:
    cs = ctx.cs
    if cs.mte2_coverage > COVERAGE_LARGE:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"Implicit MTE2 stall (coverage={cs.mte2_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE2", "VEC"),
            "MTE2",
        )
    if cs.mte3_coverage > COVERAGE_LARGE:
        return _make(
            ctx,
            "d_mte3_implicit",
            BubbleCategory.DATA_STALL,
            f"Implicit MTE3 stall (coverage={cs.mte3_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE3", "VEC"),
            "MTE3",
        )
    return None


def _check_implicit_cube(ctx: GapContext) -> Optional[BubbleResult]:
    cs = ctx.cs
    if cs.mte1_coverage > COVERAGE_LARGE:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"Implicit MTE1 stall (L1->L0, coverage={cs.mte1_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE1", "CUBE"),
            "MTE1",
        )
    if cs.mte2_coverage > COVERAGE_LARGE:
        return _make(
            ctx,
            "d_mte2_implicit",
            BubbleCategory.DATA_STALL,
            f"Implicit MTE2 stall (GM->L1, coverage={cs.mte2_coverage:.0%})",
            True,
            HINT_DOUBLE_BUFFER.format("MTE2", "CUBE"),
            "MTE2",
        )
    if cs.fixpipe_coverage > COVERAGE_LARGE:
        return _make(
            ctx,
            "cv_handoff",
            BubbleCategory.CUBE_VECTOR,
            f"Implicit FIXPIPE stall (coverage={cs.fixpipe_coverage:.0%})",
            waited="FIXPIPE",
        )
    return None


def _check_all_idle_cachemiss(ctx: GapContext) -> Optional[BubbleResult]:
    if ctx.cs.all_idle and ctx.cs.cachemiss_active:
        return _make(ctx, "s_icache_miss", BubbleCategory.STRUCTURAL, "All idle + icache miss")
    return None


def _default_result(ctx: GapContext) -> BubbleResult:
    return _make(
        ctx,
        "d_no_overlap",
        BubbleCategory.DATA_STALL,
        "Large gap without explicit WAIT_FLAG (implicit sync, no overlap)",
        True,
        "Investigate pipeline dependency for root cause",
    )


def classify_gap(ctx: GapContext, core_type: str) -> BubbleResult:
    if core_type == "cubecore":
        return _classify_gap_cube(ctx)
    return _classify_gap_vec(ctx)


def _classify_gap_vec(ctx: GapContext) -> BubbleResult:
    for checker in (
        _check_threshold_a,
        _check_threshold_b,
        _check_threshold_c_common,
        _check_threshold_c_vec,
        _check_iteration_boundary,
        _check_wait_flag_vec,
        _check_wait_flag_unavailable,
        _check_implicit_vec,
        _check_all_idle_cachemiss,
    ):
        result = checker(ctx)
        if result is not None:
            return result
    return _default_result(ctx)


def _classify_gap_cube(ctx: GapContext) -> BubbleResult:
    for checker in (
        _check_threshold_a,
        _check_threshold_b,
        _check_threshold_c_common,
        _check_threshold_c_cube,
        _check_iteration_boundary,
        _check_wait_flag_cube,
        _check_wait_flag_unavailable,
        _check_implicit_cube,
        _check_all_idle_cachemiss,
    ):
        result = checker(ctx)
        if result is not None:
            return result
    return _default_result(ctx)


# -- Analysis entry points --------------------------------------------------


def _collect_wait_flags(pipelines: Pipelines, exclude_pipe: str) -> _PipeSweep:
    flags = [e for pipe, events in pipelines.items() if pipe != exclude_pipe for e in events if e.is_wait_flag()]
    flags.sort(key=lambda e: e.ts)
    return _PipeSweep(flags)


def _analyze_bubbles(main_events, pipelines, core_type, main_pipe, total_iterations):
    bubbles = []
    sweeps = _make_sweeps(pipelines, main_pipe)
    wait_flags = _collect_wait_flags(pipelines, main_pipe)
    denom = max(1, len(main_events) - 1)

    def add_bubble(op_before, op_after, window_start, window_end, iteration_index):
        gap = window_end - window_start
        if gap <= 0:
            return
        wait_flag_event = wait_flags.first_event(window_start, window_end)
        cs = build_concurrent_state(sweeps, window_start, window_end, core_type)
        ctx = GapContext(
            gap=gap,
            op_before=op_before,
            op_after=op_after,
            cs=cs,
            has_wait_flag=wait_flag_event is not None,
            wait_flag_event=wait_flag_event,
            iteration_index=iteration_index,
            total_iterations=total_iterations,
        )
        result = classify_gap(ctx, core_type)
        result.iteration_index = iteration_index
        result.tick_start = window_start
        result.tick_end = window_end
        bubbles.append(result)

    # Leading idle: from tick 0 to the first main-pipe instruction. Processed
    # first so the forward-only sweep cursors keep seeing
    # non-decreasing starts.
    add_bubble(None, main_events[0], 0, main_events[0].ts, 0)

    # Main-pipe instructions can overlap (several RVEC* ops issued back-to-back
    # run concurrently), so a bubble is NOT simply the gap between two events
    # adjacent in ts order: an earlier-started, later-finishing event may still
    # cover it. Track the running "busy frontier" (the furthest end_ts seen so
    # far) and only emit a bubble when the next event starts beyond it.
    frontier = main_events[0].end_ts
    frontier_idx = 0
    for i in range(1, len(main_events)):
        ev = main_events[i]
        if ev.ts > frontier:
            add_bubble(
                main_events[frontier_idx],
                ev,
                frontier,
                ev.ts,
                int((frontier_idx / denom) * total_iterations),
            )
        if ev.end_ts > frontier:
            frontier = ev.end_ts
            frontier_idx = i
    return bubbles


def _build_core_report(core_id, core_type, main_pipeline_name, total_duration, bubbles) -> CoreReport:
    subtype_counts: Dict[str, int] = {}
    category_durations: Dict[str, float] = {}
    for b in bubbles:
        subtype_counts[b.sub_type] = subtype_counts.get(b.sub_type, 0) + 1
        category_durations[b.category] = category_durations.get(b.category, 0) + b.gap_us

    top_bottleneck = None
    if category_durations:
        top_bottleneck = max(category_durations, key=lambda k: category_durations[k])

    freq_ghz = frequency_ghz_map[biprof_ctx.arch]
    ticks_to_ns_coeff = 1 / (freq_ghz * 1000)

    return CoreReport(
        core_id=core_id,
        core_type=core_type,
        total_duration_us=total_duration * ticks_to_ns_coeff,
        main_pipeline=main_pipeline_name,
        bubble_count=len(bubbles),
        bubbles=bubbles,
        subtype_summary=subtype_counts,
        top_bottleneck=top_bottleneck,
    )


def analyze_pipelines(pipelines: Pipelines, core_id: str, core_type: str, total_duration: int) -> CoreReport:
    if core_type == "AIC":
        main_pipe = PIPE_CUBE
        main_pipeline_name = "CUBE"
    else:
        main_pipe = PIPE_VECTOR
        main_pipeline_name = "VECTOR"

    freq_ghz = frequency_ghz_map[biprof_ctx.arch]
    ticks_to_ns_coeff = 1 / (freq_ghz * 1000)

    main_events = pipelines.get(main_pipe, [])
    if not main_events:
        return CoreReport(
            core_id,
            core_type,
            total_duration * ticks_to_ns_coeff,
            main_pipeline_name,
            0,
            [],
            {},
            "no_main_pipeline_events",
        )

    total_iterations = max(1, len(main_events) // 2)
    bubbles = _analyze_bubbles(main_events, pipelines, core_type, main_pipe, total_iterations)

    return _build_core_report(core_id, core_type, main_pipeline_name, total_duration, bubbles)
