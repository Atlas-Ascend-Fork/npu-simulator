#!/usr/bin/env python3
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

"""data-race (RAW/WAR/WAW) detection

Input: helper record DB (a CAMODEL_LOG_PATH directory containing the record DB, or a single DB file)
Output: violation list (Violation / GmViolation / RaceCandidate) plus text formatting

All inputs come from the helper record DB:

      collect_buffer_rw_events       : MemoryRWAccesses MemoryType>0      (in-core buffer accesses)
      collect_issque_events          : ExecutedInstructions queue_type /
                                       target_pipe / consumer_pipe        (issue pipe + release ticks)
      collect_cross_core_sync_events : BAR / SET_CROSS_CORE / WAIT_FLAG_DEV/DEVI   (cross-core GM VC)
      collect_buf_sync_pipes         : GET_BUF / RLS_BUF                            (queue-sync pipe set)
      collect_intra_block_sync_events: SET/WAIT_INTRA_BLOCK(I)                      (UB cross-core VC)
      collect_gm_rw_events           : MemoryRWAccesses MemoryType=0 (GM AXI accesses)

GM / cross-core detection rules:
  10. GM_WAW_OVERLAP       — same-core GM W→W to the same address with no intervening READ consume
  11. GM_RAW_RACE        — same-core GM W→R to the same address, different instr_id
  12. GM_WAR_RACE        — same-core GM R→W to the same address, different instr_id
  13. GM_CROSS_CORE_WAW    — cross-core W×W, same addr, no cross-core sync (per-core vector clock decides HB)

Cross-pipe / same-pipe rules (sync decided by program order + SetFlag/WaitFlag chains):
  14. CROSS_PIPE_RAW_NO_SYNC — cross-pipe W→R to the same address, different pipes, no SetFlag/WaitFlag sync chain
  15. CROSS_PIPE_WAW_NO_SYNC — cross-pipe W→W to the same address, different pipes, no sync chain
  16. CROSS_PIPE_WAR_NO_SYNC — cross-pipe R→W to the same address, different pipes, no sync chain
  17. GM_CROSS_CORE_RAW      — cross-core W×R to the same address, no cross-core sync
  18. GM_CROSS_CORE_WAR      — cross-core R×W to the same address, no cross-core sync
  19. SAME_PIPE_RAW          — same-pipe W→R, same addr, different instr_id (reported only within the ≤16 instr window)
  20. SAME_PIPE_WAR          — same-pipe R→W to the same address, different instr_id
  21. SAME_PIPE_WAW          — same-pipe W→W to the same address, different instr_id

All-kind scanline+VectorClock candidates (detect_scan_races):
      25. SCAN_* — scanline+VectorClock detection entry (kept for standalone/custom pipelines;
      the default pipeline emits the same semantics as rules 14/15/16 CROSS_PIPE_*, avoiding duplicate reports)

UB cross-subcore rules (Ascend950: AICube + AIVector share one block's UB):
  22. UB_CROSS_CORE_WAW    — same-block cubecore×veccore W×W to the same UB address, no SET_INTRA_BLOCK sync
  23. UB_CROSS_CORE_RAW    — same-block cubecore×veccore W×R to the same UB address, no sync
  24. UB_CROSS_CORE_WAR    — same-block cubecore×veccore R×W to the same UB address, no sync
  Concurrency is decided by a per-subcore vector clock (SET_INTRA_BLOCK / WAIT_INTRA_BLOCK establish happens-before).
"""

import re
import os
import json
import logging
import bisect
import sqlite3
from functools import lru_cache
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional, Any, Callable
from pathlib import Path
from npusim.core.report.detections.instr_log_parser import (
    _SET_OPS,
    _WAIT_OPS,
    _core_type_to_ids,
    _core_type_to_rw_ids,
    _find_record_instr_dbs,
    collect_record_instructions,
    collect_record_sync_events,
    has_record_db_table_with_column,
)
from npusim.core.report.detections.vector_clock import (
    update_logic_time,
    update_vector_time,
    is_concurrent_partial,
    has_happens_before_relation,
    signal_store,
    signal_acquire,
    barrier_sync,
)

logger = logging.getLogger(__name__)


# ============================================================
# Part 1: record DB event collection
# ============================================================

# instr_name → pipe heuristic (some µops carry no queue_type in ExtendParams)
# Observed: vec-pipe register µops (RV_VLD/RV_VST) may lack queue_type, but an
# RV_-prefixed instr.name is a vec-pipe register op. Other common vec prefixes:
# V (VADD/VMUL), VF (VFREXP), VCONV, VCAST, VMAX, VMIN ...
INSTR_NAME_PIPE_FALLBACK = [
    (re.compile(r"^RV_"), "VEC"),  # RV_VLD/RV_VST: vec register load/store
    (re.compile(r"^V[A-Z]"), "VEC"),  # VADD/VMUL/VCONV/VCAST/VMAX/VMIN/VFREXP ...
    (re.compile(r"^AND_I2V|^OR_I2V|^XOR_I2V"), "VEC"),
]


@lru_cache(maxsize=256)
def infer_pipe_from_instr_name(name: str) -> str:
    """Infer the pipe from instr_name (fallback when the record DB has no queue_type for the instr).
    The instruction-name set is small (tens to hundreds), so lru_cache avoids re-matching the regexes per event
    (profile: 6.6M calls → cache hits are nearly free)."""
    for pat, pipe in INSTR_NAME_PIPE_FALLBACK:
        if pat.match(name):
            return pipe
    return "?"


@dataclass
class RwEvent:
    kind: str  # READ / WRITE
    core: int
    sub: int
    tick: int
    pc: int
    instr_id: int
    addr: int
    size: int
    instr_name: str
    buf_kind: str = "ub"  # ub/l1/l0c/l0a/l0b/fb/sb/ssb (all-buffer extension)
    file: str = ""
    line_no: int = 0
    file_order: int = 0
    raw: str = ""  # raw record DB row summary (locates the evidence in reports)
    core_type_id: int = 0  # record DB CoreTypeId (1=cube, 2=vector0, 3=vector1); 0 = unknown


@dataclass
class PipeIssEvent:
    """Issue-pipe event — one instruction pushed to a pipe's issue queue.
    Records which pipe issued each instruction, for the (instr_id → pipe) map."""

    core: int
    sub: int
    pipe: str  # 'mte1' / 'mte2' / 'mte3' / 'vec' / 'scalar' / 'fixp' / 'cube' (uppercase)
    instr_id: int
    pc: int
    name: str  # instruction name (MOV_SRC_TO_DST_ALIGNv2 / SET_FLAG / WAIT_FLAG / RV_VST / ...)
    tick: int  # Push tick
    file: str = ""
    line_no: int = 0


@dataclass
class SyncFlagPair:
    """SetFlag / WaitFlag pair — a synchronization primitive used to decide whether a cross-pipe race is synchronized
    A cross-pipe pair with SetFlag(src_pipe) + WaitFlag(dst_pipe) counts as synchronized and is not reported."""

    set_flag_id: int
    wait_flag_id: int
    set_pipe: str  # pipe holding SET_FLAG (source pipe)
    wait_pipe: str  # pipe holding WAIT_FLAG (destination pipe)
    set_pc: int
    wait_pc: int
    set_tick: int  # release tick (target_pipe side)
    wait_tick: int  # release tick (consumer_pipe side)
    core: int
    sub: int


def _filter_cache_refill_reads(rw_events: List[RwEvent]) -> List[RwEvent]:
    """Filter cache-refill reads: the model's dcache does read-modify-write on the UB cache line before a store,
    so a READ/WRITE pair sharing (core, sub, tick, pc, instr_id, addr, size) is a refill, not a consume.
    Without filtering, every store looks like it consumed the previous write and corrupts WAW results."""
    write_keys = set()
    for ev in rw_events:
        if ev.kind == "WRITE":
            write_keys.add((ev.core, ev.sub, ev.tick, ev.pc, ev.instr_id, ev.addr, ev.size))
    filtered = []
    for ev in rw_events:
        if ev.kind == "READ":
            key = (ev.core, ev.sub, ev.tick, ev.pc, ev.instr_id, ev.addr, ev.size)
            if key in write_keys:
                continue
        filtered.append(ev)
    return filtered


# ---- contract-v7 record DB readers (buffer rw + issue-pipe events) ----

# Low 32 bits of the composite ExecInstrId are the original instr_id.
_INSTR_ID_MASK = (1 << 32) - 1

# MemoryType → buf_kind. 0=GM (handled by collect_gm_rw_events), 9 unused.
_MEM_TYPE_TO_BUF_KIND = {1: "ub", 2: "l0a", 3: "l0b", 4: "l0c", 5: "l1"}

# ExtendParams.queue_type → pipe.
_QT_TO_PIPE = {
    "MTE1": "MTE1",
    "MTE2": "MTE2",
    "MTE3": "MTE3",
    "SIMD": "VEC",
    "SCALAR": "SCALAR",
    "FIXP": "FIXP",
    "CUBE": "CUBE",
}
_VALID_PIPES = frozenset(_QT_TO_PIPE.values())


def _collect_buffer_rw_events_from_db(input_path: str) -> List[RwEvent]:
    """Read buffer accesses from MemoryRWAccesses (MemoryType>0).

    Instruction attribution (sub/pc/instr_name) joins ExecutedInstructions on
    (low32(ExecInstrId), CoreId). A duplicated low32 within one CoreId (re-issued
    instruction) keeps the first row, which only affects reported pc/name —
    detection fields (addr/size/tick/op/core/sub/pipe) come from this table
    or CoreTypeId.
    """
    rw_events: List[RwEvent] = []
    query = (
        "SELECT AccessedAddress, AccessMode, AccessTime, MemoryType, ExecInstrId, "
        "CoreId, Size, Step FROM MemoryRWAccesses WHERE MemoryType > 0 "
        "ORDER BY AccessTime, CoreId, AccessedAddress"
    )
    instr_query = (
        "SELECT ExecInstrId, ExecInstrCoreId, CoreTypeId, ExecInstrName, SourceInstrAddr FROM ExecutedInstructions"
    )
    for db_path in _find_record_instr_dbs(Path(input_path)):
        try:
            con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            logger.warning("Failed to open record DB %s: %s", db_path, exc)
            continue
        try:
            # SourceInstrAddr is a kernel-base offset; absolute PC = start_pc + SourceInstrAddr.
            start_pc = 0
            try:
                row = con.execute("SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'start_pc'").fetchone()
                if row is not None:
                    start_pc = int(row[0])
            except (sqlite3.Error, TypeError, ValueError):
                start_pc = 0
            # (low32 ExecInstrId, CoreId) -> (core_type_id, instr_name, rel_pc)
            instr_lookup: Dict[Tuple[int, int], Tuple[int, str, int]] = {}
            for eid, ecore, etype, ename, eaddr in con.execute(instr_query):
                try:
                    key = (int(eid) & _INSTR_ID_MASK, int(ecore))
                except (TypeError, ValueError):
                    continue
                instr_lookup.setdefault(key, (int(etype), str(ename or ""), int(eaddr or 0)))
            rows = con.execute(query)
            for addr, mode, tick, mem_type, instr_id, core_id, size, step in rows:
                try:
                    core_id = int(core_id)
                    tick = int(tick)
                    addr = int(addr)
                    size = int(size) if size is not None else 0
                    # Each row covers [addr, addr + (Size-1)*Step) in the helper's
                    # per-address contract. For contiguous accesses (Step<=1, incl.
                    # NULL/0) that equals Size bytes; for strided accesses (Step>1,
                    # scatter/gather) extend the interval to the last word touched.
                    step = int(step) if step is not None else 0
                    if step > 1 and size > 0:
                        size = (size - 1) * step
                    instr_id = (int(instr_id) if instr_id is not None else 0) & _INSTR_ID_MASK
                except (TypeError, ValueError):
                    logger.warning("Invalid buffer access row in %s: Addr=%s Tick=%s", db_path, addr, tick)
                    continue
                core_type_id, name, rel_pc = instr_lookup.get((instr_id, core_id), (1, "", 0))
                pc = start_pc + rel_pc
                rw_events.append(
                    RwEvent(
                        kind="WRITE" if str(mode).upper().startswith("W") else "READ",
                        core=core_id,
                        sub=_core_type_to_rw_ids(core_id, core_type_id)[1],
                        tick=tick,
                        pc=pc,
                        instr_id=instr_id,
                        addr=addr,
                        size=size,
                        instr_name=name,
                        buf_kind=_MEM_TYPE_TO_BUF_KIND.get(int(mem_type) if mem_type is not None else 0, "ub"),
                        file=str(db_path),
                        core_type_id=core_type_id,
                        raw=(
                            f"MemoryRWAccesses MemoryType={mem_type} CoreId={core_id} "
                            f"AccessMode={mode} AccessTime={tick} Addr=0x{addr:x} Size={size} "
                            f"InstrName={name} Pc=0x{pc:x}"
                        ),
                    )
                )
        except sqlite3.Error as exc:
            logger.warning("Failed to read record DB %s: %s", db_path, exc)
        finally:
            con.close()
    return rw_events


def _collect_issque_events_from_db(
    input_path: str,
) -> Tuple[List[PipeIssEvent], List[PipeIssEvent], List[PipeIssEvent]]:
    """Build push / set_flag / wait_flag events from ExecutedInstructions
    (ExtendParams.queue_type / target_pipe / consumer_pipe).

    push: queue_type → pipe; SET_FLAG release pipe = target_pipe (source side),
    WAIT_FLAG release pipe = consumer_pipe (destination side). tick = ExecInstrTickStart
    (same DB time domain as the buffer rw AccessTime, so VC relative order is self-consistent).
    """
    push_events: List[PipeIssEvent] = []
    set_flag_events: List[PipeIssEvent] = []
    wait_flag_events: List[PipeIssEvent] = []
    for rec in collect_record_instructions(input_path):
        core, sub = _core_type_to_rw_ids(rec.core_id, rec.core_type_id)
        iid = rec.instr_id & _INSTR_ID_MASK
        queue_pipe = _QT_TO_PIPE.get(rec.queue_type)
        if rec.op in _SET_OPS:
            rel_pipe = rec.target_pipe if rec.target_pipe in _VALID_PIPES else None
            set_flag_events.append(
                PipeIssEvent(
                    core=core,
                    sub=sub,
                    pipe=rel_pipe,
                    instr_id=iid,
                    pc=rec.pc,
                    name=rec.op,
                    tick=rec.tick,
                    file=rec.file,
                )
            )
            if queue_pipe:
                push_events.append(
                    PipeIssEvent(
                        core=core,
                        sub=sub,
                        pipe=queue_pipe,
                        instr_id=iid,
                        pc=rec.pc,
                        name=rec.op,
                        tick=rec.tick,
                        file=rec.file,
                    )
                )
        elif rec.op in _WAIT_OPS:
            rel_pipe = rec.consumer_pipe if rec.consumer_pipe in _VALID_PIPES else None
            wait_flag_events.append(
                PipeIssEvent(
                    core=core,
                    sub=sub,
                    pipe=rel_pipe,
                    instr_id=iid,
                    pc=rec.pc,
                    name=rec.op,
                    tick=rec.tick,
                    file=rec.file,
                )
            )
            if queue_pipe:
                push_events.append(
                    PipeIssEvent(
                        core=core,
                        sub=sub,
                        pipe=queue_pipe,
                        instr_id=iid,
                        pc=rec.pc,
                        name=rec.op,
                        tick=rec.tick,
                        file=rec.file,
                    )
                )
        elif queue_pipe:
            push_events.append(
                PipeIssEvent(
                    core=core,
                    sub=sub,
                    pipe=queue_pipe,
                    instr_id=iid,
                    pc=rec.pc,
                    name=rec.op,
                    tick=rec.tick,
                    file=rec.file,
                )
            )
    # Sort by tick so build_pipe_map / match_sync_flags are deterministic.
    push_events.sort(key=lambda ev: ev.tick)
    set_flag_events.sort(key=lambda ev: ev.tick)
    wait_flag_events.sort(key=lambda ev: ev.tick)
    return (push_events, set_flag_events, wait_flag_events)


def _use_record_db_for_log_detectors(input_path: str) -> bool:
    """True when a contract-v7 record DB is present (MemoryRWAccesses.Size column).

    Buffer rw and issue-pipe events must share one source so their ticks stay
    in the same time domain.
    """
    return has_record_db_table_with_column(Path(input_path), "MemoryRWAccesses", "Size")


def collect_buffer_rw_events(input_path: str) -> List[RwEvent]:
    """Return the buffer rw_events read from the helper record DB.

    MemoryRWAccesses (MemoryType>0) joined with ExecutedInstructions for
    instruction attribution; empty list when no v7 DB.
    """
    if not _use_record_db_for_log_detectors(input_path):
        logger.warning("buffer rw detection skipped: no contract-v7 record DB with MemoryRWAccesses.Size")
        return []
    return _filter_cache_refill_reads(_collect_buffer_rw_events_from_db(input_path))


def collect_issque_events(input_path: str) -> Tuple[List[PipeIssEvent], List[PipeIssEvent], List[PipeIssEvent]]:
    """Read the issue-pipe events from the helper record DB and return:
      - push_events: all instruction push events (for the instr_id → pipe map)
      - set_flag_events: SET_FLAG release events (source-pipe side)
      - wait_flag_events: WAIT_FLAG release events (destination-pipe side)
    Push events for SET_FLAG/WAIT_FLAG also appear in push_events (filter by instr.name).
    """
    if not _use_record_db_for_log_detectors(input_path):
        logger.warning("issque detection skipped: no contract-v7 record DB with MemoryRWAccesses.Size")
        return ([], [], [])
    return _collect_issque_events_from_db(input_path)


def build_pipe_map(push_events: List[PipeIssEvent]) -> Dict[Tuple[int, int, int], str]:
    """Build the (core, sub, instr_id) → pipe_name map from push_events.
    Each instruction is pushed exactly once to a single pipe."""
    pipe_map: Dict[Tuple[int, int, int], str] = {}
    for ev in push_events:
        key = (ev.core, ev.sub, ev.instr_id)
        # if one instr_id appears in several pipes (abnormal), keep the first
        if key not in pipe_map:
            pipe_map[key] = ev.pipe
    return pipe_map


def match_sync_flags(set_flags: List[PipeIssEvent], wait_flags: List[PipeIssEvent]) -> List[SyncFlagPair]:
    """Pair SET_FLAG with WAIT_FLAG: group by (core, sub) and pair in tick order.
    Simplified strategy: within (core, sub), each WAIT_FLAG pairs with the nearest unpaired earlier SET_FLAG.
    In practice AscendC SetFlag<HardEvent::MTE2_V>(0) + WaitFlag<HardEvent::MTE2_V>(0) share eventID=0,
    so SET_FLAG releases on the source pipe while WAIT_FLAG releases on the destination pipe."""
    pairs: List[SyncFlagPair] = []
    by_core_sub: Dict[Tuple[int, int], Tuple[List[PipeIssEvent], List[PipeIssEvent]]] = {}
    for s in set_flags:
        by_core_sub.setdefault((s.core, s.sub), ([], []))[0].append(s)
    for w in wait_flags:
        by_core_sub.setdefault((w.core, w.sub), ([], []))[1].append(w)

    for (core, sub), (sets, waits) in by_core_sub.items():
        sets_sorted = sorted(sets, key=lambda e: e.tick)
        waits_sorted = sorted(waits, key=lambda e: e.tick)
        used_set_idx = set()
        for w in waits_sorted:
            # find the nearest unpaired SET_FLAG before w
            best_idx = None
            for i, s in enumerate(sets_sorted):
                if i in used_set_idx:
                    continue
                if s.tick > w.tick:
                    break  # sets_sorted is tick-ordered, so later entries are all later
                best_idx = i
            if best_idx is not None:
                s = sets_sorted[best_idx]
                used_set_idx.add(best_idx)
                pairs.append(
                    SyncFlagPair(
                        set_flag_id=s.instr_id,
                        wait_flag_id=w.instr_id,
                        set_pipe=s.pipe,
                        wait_pipe=w.pipe,
                        set_pc=s.pc,
                        wait_pc=w.pc,
                        set_tick=s.tick,
                        wait_tick=w.tick,
                        core=core,
                        sub=sub,
                    )
                )
    return pairs


# ============================================================
# Part 2: state tracking and detection rules
# ============================================================


@dataclass
class Interval:
    lo: int  # inclusive
    hi: int  # exclusive

    def overlap(self, other: "Interval") -> bool:
        return not (self.hi <= other.lo or other.hi <= self.lo)


@dataclass
class Violation:
    rule: str
    kernel_id: str
    core: int
    sub: int
    pc: int
    instr_id: int
    op: str  # WRITE / READ
    pipe: str
    buf_id: int
    detail: str
    severity: str = "error"
    file: str = ""
    line_no: int = 0
    raw: str = ""  # full raw log line that triggered the violation
    addr: int = 0  # current event's access interval start (for merging address ranges in aggregated output)
    size: int = 0  # current event's access interval length (for merging address ranges in aggregated output)
    prev_instr_id: int = 0  # instr_id of the prev (earlier) side of the conflict pair; 0 = unset/one-sided
    src_file: str = ""  # source file (PC symbolized; requires --object-file)
    src_line: int = 0  # source line number
    prev_pc: int = 0  # prev-side device PC (both sides of a conflict pair are symbolized separately)
    prev_raw: str = ""  # full raw log line of the prev side
    prev_src_file: str = ""  # prev-side source file
    prev_src_line: int = 0  # prev-side source line number


@dataclass
class RaceCandidate:
    """Scanline data-race candidate (scanline + VectorClock happens-before semantics):
    Endpoint sweep decides address overlap → candidate; VectorClock::IsNotHappensBefore decides if a sync chain exists.
    happens-before holds (SetFlag/WaitFlag sync) → not reported; concurrent (no sync) → reported.
    kind: RAW (W→R) / WAR (R→W) / WAW (W→W)."""

    core: int
    sub: int
    buf_kind: str
    cur_instr_id: int
    cur_pc: int
    cur_addr: int
    cur_size: int
    cur_instr_name: str
    cur_pipe: str
    prev_instr_id: int
    prev_pc: int
    prev_addr: int
    prev_size: int
    prev_instr_name: str
    prev_pipe: str
    overlap_lo: int
    overlap_hi: int
    is_concurrent: bool  # VectorClock::IsNotHappensBefore result (False = sync chain exists)
    kind: str = "WAW"  # RAW / WAR / WAW
    cur_vt: Tuple[int, ...] = ()
    prev_vt: Tuple[int, ...] = ()
    file: str = ""
    line_no: int = 0
    raw: str = ""  # full raw log line of the cur side
    prev_raw: str = ""  # full raw log line of the prev side
    cur_src_file: str = ""
    cur_src_line: int = 0
    prev_src_file: str = ""
    prev_src_line: int = 0


# Pipe → index (only pipes that camodel_v100 actually has)
PIPE_IDX = {
    "SCALAR": 0,
    "VEC": 1,
    "CUBE": 2,
    "MTE1": 3,
    "MTE2": 4,
    "MTE3": 5,
    "FIXP": 6,
}
PIPE_IDX_SIZE = 7

# Same-pipe instruction window depth = hardware issue-queue capacity; instructions inside may run concurrently.
# Instructions beyond the window are strictly FIFO-ordered, so they are not races → not reported.
# instr_id is a program-order ID; a race is reported only when |Δinstr_id| <= SAME_PIPE_INSTR_WINDOW.
SAME_PIPE_INSTR_WINDOW = 16


def _vt_to_tuple(vt: List[int]) -> Tuple[int, ...]:
    return tuple(vt[:PIPE_IDX_SIZE])


def _is_not_happens_before(t1: Tuple[int, ...], t2: Tuple[int, ...], p1: int, p2: int) -> bool:
    """Partial-order concurrency check — delegates to vector_clock.is_concurrent_partial."""
    return is_concurrent_partial(t1, t2, p1, p2)


def _pipe_idx(name: str) -> int:
    return PIPE_IDX.get(name, -1)


def _program_order_tick(
    push_tick_by_id,
    push_pc_by_pipe,
    pipe_map,
    core: int,
    sub: int,
    ev: RwEvent,
    push_pc_prefix_max: Optional[Dict[Tuple[int, int, str], Tuple[List[int], List[int]]]] = None,
) -> int:
    """Compute the program-order tick (issue tick) for an RwEvent.
    1. instr_id present in push_events → use the Push tick
    2. otherwise (µop such as RV_VST absent from push_events) → Push tick of the greatest PC ≤ ev.pc on the same pipe
    3. neither found → fall back to ev.tick
    Excludes the END_LABEL sentinel PC (0x11111111) so it is not treated as PC ≤ ev.pc.

    Exact semantics: the original took the **maximum tick** among candidates with pc <= ev.pc and not the sentinel.
    When (pc, tick) is non-monotonic (a loop gives one PC several increasing ticks), a bisect for "the last pc<=ev.pc"
    would be wrong. Instead we use the "PC-ascending + prefix max tick" index prebuilt by build_vector_clocks,
    taking the prefix max after the bisect — strictly equivalent to a linear max for any (pc, tick) order, O(log n)."""
    SENTINEL_PC = 0x11111111
    key = (core, sub, ev.instr_id)
    if key in push_tick_by_id:
        return push_tick_by_id[key]
    pipe_name = pipe_map.get(key)
    if pipe_name is None:
        pipe_name = infer_pipe_from_instr_name(ev.instr_name)
    indexed = None
    if push_pc_prefix_max is not None:
        indexed = push_pc_prefix_max.get((core, sub, pipe_name))
    if indexed is not None:
        pcs, maxes = indexed
        idx = bisect.bisect_right(pcs, ev.pc) - 1
        if idx >= 0 and maxes[idx] > 0:
            return maxes[idx]
        return ev.tick
    # no-index fallback: pairwise linear scan (exact; for direct calls/defense)
    candidates = push_pc_by_pipe.get((core, sub, pipe_name), [])
    best_tick = -1
    for pc, tick in candidates:
        if pc == SENTINEL_PC:
            continue
        if pc <= ev.pc and tick > best_tick:
            best_tick = tick
    return best_tick if best_tick > 0 else ev.tick


def _sync_order_tick(push_tick_by_id, push_pc_by_pipe, core: int, sub: int, sp: SyncFlagPair, is_set: bool) -> int:
    """Compute the program-order tick (issue tick) for SET_FLAG / WAIT_FLAG.
    WAIT_FLAG has a push event → use the Push tick of wait_flag_id
    SET_FLAG has no push event (only release) → use the greatest Push tick with PC ≤ set_pc on the same set_pipe
      (SET_FLAG is compiled with the prior source-pipe instruction and releases when that instruction completes)
    Constraint: the program-order tick must be ≤ the release tick (set_tick / wait_tick), since issue precedes release.
    fallback: release tick (set_tick / wait_tick).

    Exact semantics: take the maximum tick among candidates with pc ≤ release_pc and tick ≤ release_tick.
    That query has 2D constraints (PC + release_tick), so prefix max does not apply; sync pairs are far fewer than
    memory events, so a linear scan is kept (not a hotspot) to avoid approximations or errors."""
    if is_set:
        iid_key = (core, sub, sp.set_flag_id)
        if iid_key in push_tick_by_id:
            return push_tick_by_id[iid_key]
        candidates = push_pc_by_pipe.get((core, sub, sp.set_pipe), [])
        best_tick = -1
        for pc, tick in candidates:
            if pc <= sp.set_pc and tick <= sp.set_tick and tick > best_tick:
                best_tick = tick
        return best_tick if best_tick > 0 else sp.set_tick
    iid_key = (core, sub, sp.wait_flag_id)
    if iid_key in push_tick_by_id:
        return push_tick_by_id[iid_key]
    candidates = push_pc_by_pipe.get((core, sub, sp.wait_pipe), [])
    best_tick = -1
    for pc, tick in candidates:
        if pc <= sp.wait_pc and tick <= sp.wait_tick and tick > best_tick:
            best_tick = tick
    return best_tick if best_tick > 0 else sp.wait_tick


def _vc_order_key(t) -> Tuple[int, int]:
    """Same program_order_tick ordering: MEM first → SET → WAIT (tie-break: MEM=0, SET=1, WAIT=2)."""
    tick, tag, _ = t
    tie = 0 if tag == "MEM" else 1 if tag == "SET" else 2
    return (tick, tie)


def _apply_per_pipe_vc(
    events: List[Tuple[int, str, Any]],
    core: int,
    sub: int,
    pipe_map: Dict[Tuple[int, int, int], str],
    vt_map: Dict[Tuple[int, int, int], List[int]],
    queue_sync_pipes: Optional[set] = None,
) -> None:
    """Apply a per-pipe VectorClock to one (core, sub)'s ordered event stream.
    vc[pipe_idx] is a single VectorTime: without sync the cross-pipe dimension stays 0,
    and only at a WaitFlag is the source pipe's VectorTime merged into the destination pipe.
    queue_sync_pipes: when (core, sub) has TQue queue sync (GET_BUF/RLS_BUF), every pipe that appeared
      is treated as one vector time (they share the queue handshake), making them happens-before
      related and suppressing scanline RAW/WAR false positives."""
    if queue_sync_pipes is None:
        queue_sync_pipes = set()
    events.sort(key=_vc_order_key)
    vc: List[List[int]] = [[0] * PIPE_IDX_SIZE for _ in range(PIPE_IDX_SIZE)]
    signal_db: Dict[Tuple[int, int], List[int]] = {}
    for _, tag, ev in events:
        if tag == "MEM":
            pipe_name = pipe_map.get((core, sub, ev.instr_id))
            if pipe_name is None:
                pipe_name = infer_pipe_from_instr_name(ev.instr_name)
            pidx = _pipe_idx(pipe_name)
            if pidx < 0:
                continue
            update_logic_time(vc[pidx], pidx)
            if queue_sync_pipes:
                for qp in queue_sync_pipes:
                    qidx = _pipe_idx(qp)
                    if 0 <= qidx < PIPE_IDX_SIZE and qidx != pidx:
                        vc[pidx][qidx] = vc[pidx][pidx]
            vt_map[(core, sub, ev.instr_id)] = list(vc[pidx])
        elif tag == "SET":
            src_idx = _pipe_idx(ev.set_pipe)
            if src_idx < 0:
                continue
            update_logic_time(vc[src_idx], src_idx)
            dst_idx = _pipe_idx(ev.wait_pipe)
            signal_db[(dst_idx, ev.set_flag_id)] = list(vc[src_idx])
        elif tag == "WAIT":
            dst_idx = _pipe_idx(ev.wait_pipe)
            if dst_idx < 0:
                continue
            key = (dst_idx, ev.set_flag_id)
            vt = signal_db.pop(key, None)
            if vt is None:
                update_logic_time(vc[dst_idx], dst_idx)
                continue
            update_vector_time(vt, vc[dst_idx])
            update_logic_time(vc[dst_idx], dst_idx)


def build_vector_clocks(
    rw_events: List[RwEvent],
    pipe_map: Dict[Tuple[int, int, int], str],
    sync_pairs: List[SyncFlagPair],
    push_events: List[PipeIssEvent],
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
) -> Dict[Tuple[int, int, int], List[int]]:
    """Compute a VectorTime snapshot per RwEvent keyed by (core, sub, instr_id).
    Key point: process events in **program order** (issue order), not sim cycle order.
    Under sim cycle order the hardware reorders unsynchronized instructions and timing loses happens-before meaning.
    Program-order sources (push tick = issue tick):
      - SET_FLAG/WAIT_FLAG: SyncFlagPair.set_tick / wait_tick (release tick ≈ issue tick)
      - MEM events: if instr_id is in push_events use the Push tick; otherwise (µop such as RV_VST absent from push_events)
        use the greatest Push tick with PC ≤ the µop PC on the same pipe (the parent instruction's issue tick)
    Returns dict: (core, sub, instr_id) → VectorTime list."""
    vt_map: Dict[Tuple[int, int, int], List[int]] = {}

    # build Push indexes per (core, sub): (instr_id → push_tick), ((core,sub,pipe) → [(pc, tick)])
    push_tick_by_id: Dict[Tuple[int, int, int], int] = {}
    push_pc_by_pipe: Dict[Tuple[int, int, str], List[Tuple[int, int]]] = {}
    for pe in push_events:
        push_tick_by_id[(pe.core, pe.sub, pe.instr_id)] = pe.tick
        push_pc_by_pipe.setdefault((pe.core, pe.sub, pe.pipe), []).append((pe.pc, pe.tick))
    # the PC fallback needs (pc, tick) ascending for bisect location;
    # we also build a "prefix max tick" index so _program_order_tick, even with non-monotonic (pc, tick),
    # still recovers "max tick among pc<=ev.pc" exactly in O(log n).
    push_pc_prefix_max: Dict[Tuple[int, int, str], Tuple[List[int], List[int]]] = {}
    for key in push_pc_by_pipe:
        push_pc_by_pipe[key].sort()
        pcs = [pc for pc, _ in push_pc_by_pipe[key]]
        maxes = []
        cur = -1
        for _pc, tick in push_pc_by_pipe[key]:
            # the sentinel PC (0x11111111) never competes for max tick
            if _pc == 0x11111111:
                maxes.append(cur)
                continue
            if tick > cur:
                cur = tick
            maxes.append(cur)
        push_pc_prefix_max[key] = (pcs, maxes)

    # group SET_FLAG / WAIT_FLAG / RwEvent by (core, sub), each carrying its program-order tick
    by_core_sub: Dict[Tuple[int, int], List[Tuple[int, str, Any]]] = {}
    for sp in sync_pairs:
        by_core_sub.setdefault((sp.core, sp.sub), []).append(
            (_sync_order_tick(push_tick_by_id, push_pc_by_pipe, sp.core, sp.sub, sp, True), "SET", sp)
        )
        by_core_sub.setdefault((sp.core, sp.sub), []).append(
            (_sync_order_tick(push_tick_by_id, push_pc_by_pipe, sp.core, sp.sub, sp, False), "WAIT", sp)
        )
    for ev in rw_events:
        po_tick = _program_order_tick(
            push_tick_by_id, push_pc_by_pipe, pipe_map, ev.core, ev.sub, ev, push_pc_prefix_max=push_pc_prefix_max
        )
        by_core_sub.setdefault((ev.core, ev.sub), []).append((po_tick, "MEM", ev))

    # TQue queue sync (GET_BUF/RLS_BUF) present ⇒ all participating pipes share one
    # queue-handshake vector time (e.g. MTE2/VEC/MTE3 in baseline), making them happens-before related
    # queue_sync_pipes is derived by collect_buf_sync_pipes from GET_BUF/RLS_BUF in the instr dump.

    for (core, sub), events in by_core_sub.items():
        _apply_per_pipe_vc(
            events, core, sub, pipe_map, vt_map, queue_sync_pipes=(queue_sync_pipes or {}).get((core, sub))
        )

    return vt_map


def _race_kind(cur: RwEvent, prev: RwEvent) -> Optional[str]:
    """Decide the race kind from the cur/prev read-write mix; READ×READ is no race and returns None."""
    if cur.kind == "WRITE" and prev.kind == "WRITE":
        return "WAW"
    if cur.kind == "READ" and prev.kind == "WRITE":
        return "RAW"
    if cur.kind == "WRITE" and prev.kind == "READ":
        return "WAR"
    return None


def _build_pipe_caches(
    rw_events: List[RwEvent], pipe_map: Dict[Tuple[int, int, int], str], vt_map: Dict[Tuple[int, int, int], List[int]]
):
    """Prebuild a per-event (pipe, vt_tuple, pipe_idx) cache for reuse in scanline pairing.

    The pipe comes from the push map / instr_name heuristic; one event can be paired repeatedly against several
    later events in history, and the original re-ran the regex per pair (a profile hotspot).
    """
    pipe_cache: Dict[int, str] = {}
    vt_cache: Dict[Tuple[int, int, int], Tuple[int, ...]] = {}
    pidx_cache: Dict[int, int] = {}
    for ev in rw_events:
        pipe_cache[id(ev)] = _event_pipe(pipe_map, ev.core, ev.sub, ev)
        vt = vt_map.get((ev.core, ev.sub, ev.instr_id), [0] * PIPE_IDX_SIZE)
        vt_cache[(ev.core, ev.sub, ev.instr_id)] = _vt_to_tuple(vt)
        pidx_cache[id(ev)] = _pipe_idx(pipe_cache[id(ev)])
    return (pipe_cache, vt_cache, pidx_cache)


def _scan_race_pair(
    core: int,
    sub: int,
    buf_kind: str,
    ev: RwEvent,
    cur_lo: int,
    cur_hi: int,
    prev: RwEvent,
    pipe_map: Dict[Tuple[int, int, int], str],
    vt_map: Dict[Tuple[int, int, int], List[int]],
    candidates: List[RaceCandidate],
    want: Callable[[str], bool],
    pipe_cache: Optional[Dict[int, str]] = None,
    vt_cache: Optional[Dict[Tuple[int, int, int], Tuple[int, ...]]] = None,
    pidx_cache: Optional[Dict[int, int]] = None,
) -> None:
    """Decide and record one RAW/WAR/WAW candidate (only when want(kind) holds and the pair is concurrent without sync).

    Cache arguments come from _race_scanline (pipe/VT parsed once per event); semantics match the old
    per-pair parsing exactly, and fall back to per-pair parsing when absent (for direct UT calls)."""
    if prev.instr_id == ev.instr_id:
        return
    prev_lo = prev.addr
    prev_hi = prev.addr + prev.size
    if prev_hi <= cur_lo:
        return  # history interval already ended, no overlap
    kind = _race_kind(ev, prev)
    if kind is None or not want(kind):
        return  # READ×READ is no race, or kind not handled by this entry
    ov_lo = max(cur_lo, prev_lo)
    ov_hi = min(cur_hi, prev_hi)
    if pipe_cache is not None:
        # Note: dict.get(key, default) evaluates default eagerly, so it cannot be a lazy fallback
        # (otherwise every pair re-calls _event_pipe and the cache is pointless). Look up first, then fall back.
        cur_pipe = pipe_cache.get(id(ev))
        if cur_pipe is None:
            cur_pipe = _event_pipe(pipe_map, core, sub, ev)
        prev_pipe = pipe_cache.get(id(prev))
        if prev_pipe is None:
            prev_pipe = _event_pipe(pipe_map, core, sub, prev)
        cur_pidx = pidx_cache.get(id(ev), -1)
        prev_pidx = pidx_cache.get(id(prev), -1)
    else:
        cur_pipe = _event_pipe(pipe_map, core, sub, ev)
        prev_pipe = _event_pipe(pipe_map, core, sub, prev)
        cur_pidx = _pipe_idx(cur_pipe)
        prev_pidx = _pipe_idx(prev_pipe)
    if cur_pidx == prev_pidx or cur_pidx < 0 or prev_pidx < 0:
        return  # same pipe is not a cross-pipe race
    if vt_cache is not None:
        key_cur = (ev.core, ev.sub, ev.instr_id)
        key_prev = (prev.core, prev.sub, prev.instr_id)
        cur_vt_t = vt_cache.get(key_cur, (0,) * PIPE_IDX_SIZE)
        prev_vt_t = vt_cache.get(key_prev, (0,) * PIPE_IDX_SIZE)
    else:
        cur_vt_t = _vt_to_tuple(vt_map.get((core, sub, ev.instr_id), [0] * PIPE_IDX_SIZE))
        prev_vt_t = _vt_to_tuple(vt_map.get((core, sub, prev.instr_id), [0] * PIPE_IDX_SIZE))
    if not _is_not_happens_before(cur_vt_t, prev_vt_t, cur_pidx, prev_pidx):
        return  # sync chain exists, do not report
    candidates.append(
        RaceCandidate(
            core=core,
            sub=sub,
            buf_kind=buf_kind,
            cur_instr_id=ev.instr_id,
            cur_pc=ev.pc,
            cur_addr=cur_lo,
            cur_size=ev.size,
            cur_instr_name=ev.instr_name,
            cur_pipe=cur_pipe,
            prev_instr_id=prev.instr_id,
            prev_pc=prev.pc,
            prev_addr=prev_lo,
            prev_size=prev.size,
            prev_instr_name=prev.instr_name,
            prev_pipe=prev_pipe,
            overlap_lo=ov_lo,
            overlap_hi=ov_hi,
            is_concurrent=True,
            kind=kind,
            cur_vt=cur_vt_t,
            prev_vt=prev_vt_t,
            file=ev.file,
            line_no=ev.line_no,
            raw=ev.raw,
            prev_raw=prev.raw,
        )
    )


def _race_scanline(
    rw_events: List[RwEvent],
    pipe_map: Dict[Tuple[int, int, int], str],
    sync_pairs: List[SyncFlagPair],
    push_events: List[PipeIssEvent],
    want: Callable[[str], bool],
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
) -> List[RaceCandidate]:
    """Scanline algorithm + VectorClock happens-before sync check (shared internal scanner):
    1. split every READ/WRITE into start/end endpoints and sort by ascending addr
    2. sweep endpoints: on start, overlapping current/history events → race candidates (kind from the R/W mix);
       on end, remove from history
    3. filter each candidate with VectorClock::IsNotHappensBefore: sync chain → not reported, concurrent → reported
    The `want(kind)` predicate selects which kinds to keep (each public entry passes RAW/WAR/WAW itself).
    Grouping by (core, sub, buf_kind) avoids cross-core / cross-buffer false pairing."""
    candidates: List[RaceCandidate] = []
    vt_map = build_vector_clocks(rw_events, pipe_map, sync_pairs, push_events, queue_sync_pipes=queue_sync_pipes)

    # parse pipe and VT snapshot once per event for reuse by _scan_race_pair: one event
    # can overlap several later events in history, and the original repeated regex matching/tuple building per pair
    # (profile: 6.6M _event_pipe + infer_pipe regex calls and 4M _vt_to_tuple calls).
    cached = _build_pipe_caches(rw_events, pipe_map, vt_map)

    by_core_sub_buf: Dict[Tuple[int, int, str], List[RwEvent]] = {}
    for ev in rw_events:
        by_core_sub_buf.setdefault((ev.core, ev.sub, ev.buf_kind), []).append(ev)

    for (core, sub, buf_kind), events in by_core_sub_buf.items():
        # endpoints: (addr, is_start, file_order, ev)
        endpoints: List[Tuple[int, int, int, RwEvent]] = []
        for ev in events:
            endpoints.append((ev.addr, 1, ev.file_order, ev))  # start
            endpoints.append((ev.addr + ev.size, 0, ev.file_order, ev))  # end
        # sort by ascending addr; at equal addr, end before start (avoids false hits on touching endpoints)
        endpoints.sort(key=lambda t: (t[0], t[1], t[2]))

        history = _PendingTree()
        by_instr: Dict[int, List["_INode"]] = {}
        # the old code iterated in history insertion order (= sweep order); the interval tree queries by address order,
        # so we re-sort by seq (insertion order) to restore the exact output order
        _scan_timeline_history_order = lambda nodes: sorted(nodes, key=lambda nd: nd.seq)
        for addr, is_start, _, ev in endpoints:
            if is_start:
                cur_lo = ev.addr
                cur_hi = ev.addr + ev.size
                for nd in _scan_timeline_history_order(history.query(cur_lo, cur_hi)):
                    _scan_race_pair(
                        core,
                        sub,
                        buf_kind,
                        ev,
                        cur_lo,
                        cur_hi,
                        nd.ev,
                        pipe_map,
                        vt_map,
                        candidates,
                        want,
                        pipe_cache=cached[0],
                        vt_cache=cached[1],
                        pidx_cache=cached[2],
                    )
                node = history.insert(ev)
                by_instr.setdefault(ev.instr_id, []).append(node)
            else:
                # old implementation: history = [h for h in history if h.instr_id != ev.instr_id]
                # — removes all intervals of that instr_id at once. The by_instr index locates them in O(1)
                # and deletes node by node in O(log n), avoiding a linear rebuild of the whole table.
                for node in by_instr.pop(ev.instr_id, ()):
                    if node.ev.instr_id == ev.instr_id:  # defensive: index and nodes must agree
                        history.delete_node(node)

    return candidates


def detect_scan_races(
    rw_events: List[RwEvent],
    pipe_map: Dict[Tuple[int, int, int], str],
    sync_pairs: List[SyncFlagPair],
    push_events: List[PipeIssEvent],
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
) -> List[RaceCandidate]:
    """Scanline data races: one scanline+VectorClock pass collects all RAW/WAR/WAW candidates.

    Builds the vector clocks once and sweeps the event endpoints once."""
    return _race_scanline(
        rw_events, pipe_map, sync_pairs, push_events, want=lambda k: True, queue_sync_pipes=queue_sync_pipes
    )


# ============================================================
# Part 2C: cross-pipe race detection
# ============================================================
# Unlike tick-ordered rules 10/11/12, this judges cross-pipe dependencies + missing sync in code pc order:
#   1. take the (instr_id → pipe) map from push_events
#   2. take sync pairs from SET_FLAG/WAIT_FLAG releases
#   3. in pc order find cross-pipe RAW/WAW/WAR (different pipes writing/reading the same addr)
#   4. check for a SetFlag(src_pipe) + WaitFlag(dst_pipe) pair between the two accesses
#      present → correctly synchronized, not reported
#      absent → cross-pipe race, report CROSS_PIPE_*_NO_SYNC
#
# Note: camodel_v100 reorders unsynchronized cross-pipe instructions, so tick order is untrustworthy; use pc order.


def _has_sync_chain(
    pairs: List[SyncFlagPair],
    core: int,
    sub: int,
    src_pipe: str,
    dst_pipe: str,
    max_depth: int = 4,
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
) -> bool:
    """Check whether src_pipe → dst_pipe has a sync chain (intermediate pipes may relay).
    E.g. a chained MTE2 → VEC → MTE3 sync counts even without a direct MTE2 → MTE3 pair.
    max_depth bounds recursion to avoid infinite loops.
    queue_sync_pipes: when (core, sub) has GET_BUF/RLS_BUF (TQue tokens), the TQue pipeline counts as
      having cross-pipe queue sync — AscendC EnQue/DeQue output has no explicit SET/WAIT,
      sync is a buf_id counting handshake; without it many high-confidence false positives appear (e.g. baseline)."""
    if queue_sync_pipes and (core, sub) in queue_sync_pipes:
        return True
    if src_pipe == dst_pipe:
        return True
    # recursive relay-chain query with a per-call private memo (keyed by max_depth) to avoid cross-call bleed
    # (results differ across pairs / max_depth) while still avoiding repeated recursion.
    memo: Dict[Tuple[str, str, int], bool] = {}

    def _recur(cur_pipe: str, target: str, depth: int) -> bool:
        if depth <= 0:
            return False
        key = (cur_pipe, target, depth)
        cached = memo.get(key)
        if cached is not None:
            return cached
        result = False
        for p in pairs:
            if p.core != core or p.sub != sub:
                continue
            if p.set_pipe != cur_pipe:
                continue
            if p.wait_pipe == target:
                result = True
                break
            if _recur(p.wait_pipe, target, depth - 1):
                result = True
                break
        memo[key] = result
        return result

    return _recur(src_pipe, dst_pipe, max_depth)


def _event_pipe(pipe_map: Dict[Tuple[int, int, int], str], core: int, sub: int, ev: RwEvent) -> str:
    """Resolve an event's pipe: push map first, then the instr_name heuristic."""
    pipe = pipe_map.get((core, sub, ev.instr_id))
    if pipe is None:
        pipe = infer_pipe_from_instr_name(ev.instr_name)
    return pipe


def _cross_pipe_conflict(
    pipe_map: Dict[Tuple[int, int, int], str],
    sync_pairs: List[SyncFlagPair],
    core: int,
    sub: int,
    ev: RwEvent,
    other: RwEvent,
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
    bar_ticks: Optional[Dict[int, List[int]]] = None,
) -> bool:
    """Cross-pipe with no sync chain → treat as a race (return True); otherwise not reported."""
    ev_pipe = _event_pipe(pipe_map, core, sub, ev)
    other_pipe = _event_pipe(pipe_map, core, sub, other)
    if other_pipe == ev_pipe or other_pipe == "?" or ev_pipe == "?":
        return False
    if _has_sync_chain(sync_pairs, core, sub, other_pipe, ev_pipe, queue_sync_pipes=queue_sync_pipes):
        return False
    if bar_ticks is not None:
        bars = bar_ticks.get(core)
        if bars:
            # SyncAll's BAR is a full barrier: a same-core BAR between two events already establishes their order.
            lo = min(other.tick, ev.tick)
            hi = max(other.tick, ev.tick)
            idx = bisect.bisect_right(bars, lo)
            if idx < len(bars) and bars[idx] <= hi:
                return False
    return True


class _INode:
    """PendingTree node — one pending interval (ev.addr, ev.addr+ev.size).
    seq: insertion index = timeline order, used to restore the old "earliest timeline overlap" semantics."""

    __slots__ = ("lo", "hi", "seq", "ev", "left", "right", "prio", "max_hi")

    def __init__(self, ev, lo, hi, seq):
        self.lo = lo
        self.hi = hi
        self.seq = seq
        self.ev = ev
        self.left = None
        self.right = None
        self.prio = hash((lo, seq))  # deterministic pseudo-random priority (int hash, unsalted)
        self.max_hi = hi


def _inode_max_hi(n: Optional["_INode"]) -> int:
    return n.max_hi if n is not None else -1


def _inode_upd(n: "_INode") -> None:
    n.max_hi = max(n.hi, _inode_max_hi(n.left), _inode_max_hi(n.right))


def _inode_rot_right(n: "_INode") -> "_INode":
    left = n.left
    n.left = left.right
    left.right = n
    _inode_upd(n)
    _inode_upd(left)
    return left


def _inode_rot_left(n: "_INode") -> "_INode":
    right = n.right
    n.right = right.left
    right.left = n
    _inode_upd(n)
    _inode_upd(right)
    return right


def _inode_insert(n: Optional["_INode"], node: "_INode") -> "_INode":
    if n is None:
        return node
    if node.lo < n.lo or (node.lo == n.lo and node.seq < n.seq):
        n.left = _inode_insert(n.left, node)
        if n.left.prio < n.prio:
            n = _inode_rot_right(n)
    else:
        n.right = _inode_insert(n.right, node)
        if n.right.prio < n.prio:
            n = _inode_rot_left(n)
    _inode_upd(n)
    return n


def _inode_delete(n: Optional["_INode"], lo: int, seq: int) -> Optional["_INode"]:
    if n is None:
        return None
    if lo < n.lo or (lo == n.lo and seq < n.seq):
        n.left = _inode_delete(n.left, lo, seq)
    elif lo > n.lo or (lo == n.lo and seq > n.seq):
        n.right = _inode_delete(n.right, lo, seq)
    else:
        if n.left is None:
            return n.right
        if n.right is None:
            return n.left
        if n.left.prio < n.right.prio:
            n = _inode_rot_right(n)
            n.right = _inode_delete(n.right, lo, seq)
        else:
            n = _inode_rot_left(n)
            n.left = _inode_delete(n.left, lo, seq)
    _inode_upd(n)
    return n


def _inode_query(n: Optional["_INode"], qlo: int, qhi: int, out: List["_INode"]) -> None:
    """Collect all nodes overlapping [qlo, qhi) into out (interval-tree overlap query, pruned by max_hi)."""
    if n is None or n.max_hi <= qlo:
        return
    if n.lo >= qhi:
        # every lo in the right subtree is >= n.lo >= qhi, so no overlap; search the left subtree only
        _inode_query(n.left, qlo, qhi, out)
        return
    if n.hi > qlo:
        out.append(n)
    _inode_query(n.left, qlo, qhi, out)
    _inode_query(n.right, qlo, qhi, out)


class _PendingTree:
    """Pending event set organized by address interval (treap interval tree, stdlib only).

    Semantics match the old pending list, but an overlap query visits only truly overlapping nodes
    instead of scanning all pending — cutting _scan_timeline from O(n²) to O((n+k) log n)
    (k = real overlap pairs). Nodes are ordered by (lo, seq); overlaps are returned in seq (timeline) order
    to restore the old "earliest hit" behavior.
    """

    def __init__(self):
        self._root: Optional[_INode] = None
        self._seq = 0

    def insert(self, ev: Any) -> "_INode":
        node = _INode(ev, ev.addr, ev.addr + ev.size, self._seq)
        self._seq += 1
        self._root = _inode_insert(self._root, node)
        return node

    def delete_node(self, node: "_INode") -> None:
        self._root = _inode_delete(self._root, node.lo, node.seq)

    def query(self, qlo: int, qhi: int) -> List["_INode"]:
        out: List[_INode] = []
        _inode_query(self._root, qlo, qhi, out)
        return out

    def __len__(self) -> int:
        return self._seq  # for debugging/assertions only


def _first_pending_pass(
    node_list: List["_INode"],
    ev: Any,
    conflict: Optional[Callable],
    consumed: Optional[Callable],
    pending_reads: Optional[List[Any]],
):
    """Find the first overlap candidate (timeline order) passing every gate (instr_id/consumed/conflict)
    pending event, matching the old for-loop continue/break semantics."""
    for nd in sorted(node_list, key=lambda nd: nd.seq):
        if nd.ev.instr_id == ev.instr_id:
            continue
        if consumed is not None and consumed(nd.ev, pending_reads):
            continue
        if conflict is not None and not conflict(ev, nd.ev):
            continue
        return nd.ev
    return None


def _scan_timeline(
    events: List[Any],
    rules: Dict[str, str],
    make_violation: Callable,
    conflict: Callable = None,
    consumed: Callable = None,
    skip: Callable = None,
) -> None:
    """Pending read/write scanner for one timeline (shared by cross-pipe / same-pipe / GM).

    Sweep events in tick order, maintaining pending_writes / pending_reads (interval trees):
      - WRITE: overlaps and conflicts with a pending write → WAW; with a pending read → WAR
      - READ:  overlaps and conflicts with a pending write → RAW
    Each hit is reported once (break); pending reads covered by this write are removed (consume semantics).

    Behaviour differences are injected via callbacks:
      make_violation(rule, ev, interval, other, other_interval, kind_tag)
      conflict(ev, other)      — whether it is a race (e.g. cross-pipe with no sync chain / always true for same pipe)
      consumed(pending_write, pending_reads) — whether that pending write was consumed (e.g. GM WAW)
      skip(ev, events)         — whether to skip this event (e.g. same-tick atomic block on the same VEC pipe)
    `rules` maps WAW/WAR/RAW to each detector's rule name.

    Performance: pending events live in a _PendingTree, so each event only queries/removes candidates it truly overlaps
    instead of scanning all pending linearly. Overlap candidates are ordered by seq (timeline order) and the first one
    passing every gate is taken — exactly the old continue/break ordering. consumed is GM-only,
    and pending_reads is maintained only when needed (kept in sync with the tree) to avoid pointless O(n) rebuilds.
    """
    pending_writes = _PendingTree()
    pending_reads = _PendingTree()
    # consumed (GM WAW) needs the current pending_reads as a list, maintained in parallel with the tree
    reads_view: Optional[List[Any]] = [] if consumed is not None else None
    for ev in events:
        if skip is not None and skip(ev, events):
            continue
        ev_lo = ev.addr
        ev_hi = ev.addr + ev.size
        if ev.kind == "WRITE":
            best = _first_pending_pass(pending_writes.query(ev_lo, ev_hi), ev, conflict, consumed, reads_view)
            if best is not None and rules.get("WAW"):
                make_violation(
                    rules["WAW"], ev, Interval(ev_lo, ev_hi), best, Interval(best.addr, best.addr + best.size), "WAW"
                )
            best = _first_pending_pass(pending_reads.query(ev_lo, ev_hi), ev, conflict, None, None)
            if best is not None and rules.get("WAR"):
                make_violation(
                    rules["WAR"], ev, Interval(ev_lo, ev_hi), best, Interval(best.addr, best.addr + best.size), "WAR"
                )
            pending_writes.insert(ev)
            for nd in pending_reads.query(ev_lo, ev_hi):
                pending_reads.delete_node(nd)
            if reads_view is not None:
                reads_view = [pr for pr in reads_view if pr.addr >= ev_hi or pr.addr + pr.size <= ev_lo]
        elif ev.kind == "READ":
            best = _first_pending_pass(pending_writes.query(ev_lo, ev_hi), ev, conflict, None, None)
            if best is not None and rules.get("RAW"):
                make_violation(
                    rules["RAW"], ev, Interval(ev_lo, ev_hi), best, Interval(best.addr, best.addr + best.size), "RAW"
                )
            for nd in pending_writes.query(ev_lo, ev_hi):
                pending_writes.delete_node(nd)
            pending_reads.insert(ev)
            if reads_view is not None:
                reads_view.append(ev)


def detect_cross_pipe_races(
    rw_events: List[RwEvent],
    pipe_map: Dict[Tuple[int, int, int], str],
    sync_pairs: List[SyncFlagPair],
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
    bar_ticks: Optional[Dict[int, List[int]]] = None,
) -> List[Violation]:
    """Rules 14/15/16: cross-pipe RAW/WAW/WAR without sync.
    Group by (core, sub, buf_kind), sweep events in tick order, and find cross-pipe dependencies:
      - WAW: write A (pipe1) → write B (pipe2) at the same addr, different pipes, no SetFlag/WaitFlag (pipe1→pipe2) sync
      - RAW: write A (pipe1) → read B (pipe2) at the same addr, different pipes, no sync chain
      - WAR: read A (pipe1) → write B (pipe2) at the same addr, different pipes, no sync chain
    Same pipe does not count (FIFO-serialized).
    Note: burst splits of one instr_id (e.g. RV_VST 64×4B) do not count."""
    hits: List[Violation] = []

    by_core_sub_buf: Dict[Tuple[int, int, str], List[RwEvent]] = {}
    for ev in rw_events:
        by_core_sub_buf.setdefault((ev.core, ev.sub, ev.buf_kind), []).append(ev)

    for (core, sub, buf_kind), events in by_core_sub_buf.items():
        events_sorted = sorted(events, key=lambda e: (e.tick, e.file_order))

        def _make_violation(rule, ev, interval, other, other_interval, kind_tag):
            other_pipe = _event_pipe(pipe_map, core, sub, other)
            ev_pipe = _event_pipe(pipe_map, core, sub, ev)
            hits.append(
                Violation(
                    rule=rule,
                    kernel_id=_core_label(core, sub, ev.file),
                    core=core,
                    sub=sub,
                    pc=ev.pc,
                    instr_id=ev.instr_id,
                    prev_instr_id=other.instr_id,
                    op=ev.kind,
                    pipe=ev_pipe,
                    buf_id=-1,
                    detail=f"{ev.kind} instr_id={ev.instr_id} pipe={ev_pipe} "
                    f"range=[0x{interval.lo:x},0x{interval.hi:x}) cycle={ev.tick} overlaps "
                    f"{other.kind} instr_id={other.instr_id} pipe={other_pipe} "
                    f"range=[0x{other_interval.lo:x},0x{other_interval.hi:x}) cycle={other.tick}, "
                    f"no SetFlag/WaitFlag chain ({other_pipe}->{ev_pipe}) sync",
                    file=ev.file,
                    line_no=ev.line_no,
                    raw=ev.raw,
                    severity="error",
                    addr=interval.lo,
                    size=interval.hi - interval.lo,
                    prev_pc=other.pc,
                    prev_raw=other.raw,
                )
            )

        def _conflict(ev, other):
            return _cross_pipe_conflict(
                pipe_map, sync_pairs, core, sub, ev, other, queue_sync_pipes=queue_sync_pipes, bar_ticks=bar_ticks
            )

        _scan_timeline(
            events_sorted,
            {"WAW": "CROSS_PIPE_WAW_NO_SYNC", "RAW": "CROSS_PIPE_RAW_NO_SYNC", "WAR": "CROSS_PIPE_WAR_NO_SYNC"},
            make_violation=_make_violation,
            conflict=_conflict,
        )

    return hits


# ============================================================
# Part 2D: same-pipe race detection
# ============================================================
# Complements Part 2C's cross-pipe detection: group by (core, sub, buf_kind, pipe) and, within a single pipe's FIFO,
# find same-address RAW (W→R) / WAR (R→W) dependencies in tick order.
# A single pipe's FIFO usually preserves order, so these are normal producer-consumer dependencies; but per the
# "cross core / cross pipe / intra-pipeline must all be detected" completeness requirement they are still reported.
# Same-pipe WAW is covered by SAME_PIPE_WAW (rule 21); here RAW/WAR/WAW are detected together.


def _same_pipe_instr_window_conflict(ev: RwEvent, other: RwEvent) -> bool:
    """Same-pipe race gate: both instrs must lie in the hardware issue window (|Δinstr_id| <= SAME_PIPE_INSTR_WINDOW)
    are they possibly concurrent and reported; past the window the FIFO is strictly ordered, so nothing is reported."""
    return abs(ev.instr_id - other.instr_id) <= SAME_PIPE_INSTR_WINDOW


def _same_pipe_violation(
    rule: str,
    core: int,
    sub: int,
    pipe: str,
    ev: RwEvent,
    interval: Interval,
    other_kind: str,
    other: RwEvent,
    other_interval: Interval,
    kind_tag: str,
) -> Violation:
    """Build a same-pipe RAW/WAR data-race Violation (FIFO-ordered completeness report)."""
    return Violation(
        rule=rule,
        kernel_id=_core_label(core, sub, ev.file),
        core=core,
        sub=sub,
        pc=ev.pc,
        instr_id=ev.instr_id,
        prev_instr_id=other.instr_id,
        op="WRITE" if ev.kind == "WRITE" else "READ",
        pipe=pipe,
        buf_id=-1,
        detail=f"{ev.kind} instr_id={ev.instr_id} pipe={pipe} "
        f"range=[0x{interval.lo:x},0x{interval.hi:x}) cycle={ev.tick} overlaps "
        f"same-pipe {other_kind} instr_id={other.instr_id} "
        f"range=[0x{other_interval.lo:x},0x{other_interval.hi:x}) cycle={other.tick}, "
        f"same-pipe {kind_tag} (FIFO-ordered, completeness report)",
        file=ev.file,
        line_no=ev.line_no,
        raw=ev.raw,
        severity="error",
        addr=interval.lo,
        size=interval.hi - interval.lo,
        prev_pc=other.pc,
        prev_raw=other.raw,
    )


def detect_same_pipe_races(
    rw_events: List[RwEvent],
    pipe_map: Dict[Tuple[int, int, int], str],
    queue_sync_pipes: Optional[Dict[Tuple[int, int], set]] = None,
) -> List[Violation]:
    """Rules 19/20/21: same-pipe RAW/WAR/WAW data races (instruction-window gated).
    Group by (core, sub, buf_kind, pipe) and sweep events in tick order:
      - RAW: WRITE → READ at the same addr, different instr_id, same pipe
      - WAR: READ → WRITE at the same addr, different instr_id, same pipe
      - WAW: WRITE → WRITE at the same addr, different instr_id, same pipe
    Instruction-window gate: a race is reported only when |Δinstr_id| <= SAME_PIPE_INSTR_WINDOW (16) —
    instrs within the issue-queue capacity may run concurrently; beyond it the FIFO is strictly ordered — no race.
    Burst splits of one instr_id (e.g. RV_VST 64×4B) are not races.
    Events whose pipe is unknown ("?") are skipped to avoid false positives."""
    hits: List[Violation] = []

    by_core_sub_buf_pipe: Dict[Tuple[int, int, str, str], List[RwEvent]] = {}
    for ev in rw_events:
        pipe = pipe_map.get((ev.core, ev.sub, ev.instr_id))
        if pipe is None:
            pipe = infer_pipe_from_instr_name(ev.instr_name)
        if pipe == "?":
            continue  # pipe unknown, skip
        by_core_sub_buf_pipe.setdefault((ev.core, ev.sub, ev.buf_kind, pipe), []).append(ev)

    for (core, sub, buf_kind, pipe), events in by_core_sub_buf_pipe.items():
        events_sorted = sorted(events, key=lambda e: (e.tick, e.file_order))

        def _make_violation(rule, ev, interval, other, other_interval, kind_tag):
            hits.append(
                _same_pipe_violation(rule, core, sub, pipe, ev, interval, other.kind, other, other_interval, kind_tag)
            )

        # TQue-synchronized buffer (GET_BUF/RLS_BUF): producer→consumer dependencies are guaranteed by the buf_id
        # counting handshake, so same-pipe FIFO RAW/WAR are normal and not reported; only WAW is kept for completeness.
        rules: Dict[str, str] = {"WAW": "SAME_PIPE_WAW", "RAW": "SAME_PIPE_RAW", "WAR": "SAME_PIPE_WAR"}
        if queue_sync_pipes and (core, sub) in queue_sync_pipes:
            rules = {"WAW": "SAME_PIPE_WAW"}

        # On the VEC pipe, RV_VLD/RV_VST sizes are in half-words (2B), not bytes, and addr is relative.
        # A single VEC µop (same-tick load+store block) is atomic: same-tick R/W does not form an independent
        # race (e.g. Adds reads then writes the whole UB, FIFO-ordered). Genuine cross-tick WAW is still checked.
        # Original skip predicate: at this tick there are other events (different instr_id) and all of them have kind
        # != ev.kind — equivalent to: at least 2 distinct instrs at this tick, and events of this kind at this tick
        # share a single instr. We precompute per tick (distinct instr count, distinct instr count per kind),
        # avoiding a linear scan of the whole group per event.
        vec_skip_tick: Dict[int, Tuple[int, Dict[str, int]]] = {}
        if pipe == "VEC":
            tick_events: Dict[int, List[RwEvent]] = {}
            for e in events_sorted:
                tick_events.setdefault(e.tick, []).append(e)
            for t, evs in tick_events.items():
                distinct_instr = {e.instr_id for e in evs}
                if len(distinct_instr) < 2:
                    continue
                kinds_distinct: Dict[str, int] = {}
                for e in evs:
                    kinds_distinct.setdefault(e.kind, set()).add(e.instr_id)
                vec_skip_tick[t] = (len(distinct_instr), {k: len(v) for k, v in kinds_distinct.items()})

        def _skip_vec_same_tick(ev, events):
            if pipe != "VEC":
                return False
            info = vec_skip_tick.get(ev.tick)
            if info is None:
                return False
            return info[1].get(ev.kind, 0) == 1

        # instruction-window gate: report only when |Δinstr_id| <= 16 (issue-queue capacity allows concurrency),
        # beyond the window the FIFO is strictly ordered and there is no race.
        _scan_timeline(
            events_sorted,
            rules,
            make_violation=_make_violation,
            conflict=_same_pipe_instr_window_conflict,
            skip=_skip_vec_same_tick,
        )

    return hits


# ============================================================
# Part 3: main flow — detection orchestration + output
# ============================================================


@dataclass
class RaceContext:
    """Aggregate every collected log artifact for RaceRule to read."""

    rw_events: List[RwEvent] = field(default_factory=list)
    pipe_map: Dict[Tuple[int, int, int], str] = field(default_factory=dict)
    sync_pairs: List[SyncFlagPair] = field(default_factory=list)
    push_events: List[PipeIssEvent] = field(default_factory=list)
    queue_sync_pipes: Dict[Tuple[int, int], set] = field(default_factory=dict)
    gm_events: List[Any] = field(default_factory=list)
    cc_sync_events: List[Any] = field(default_factory=list)
    intra_sync_events: List[Any] = field(default_factory=list)
    bar_ticks: Dict[int, List[int]] = field(default_factory=dict)


class RaceRule(ABC):
    """Base class for data-race detection rules — Strategy pattern."""

    @abstractmethod
    def check(self, ctx: RaceContext) -> List[Any]: ...


class CrossPipeRaceRule(RaceRule):
    def check(self, ctx):
        return detect_cross_pipe_races(
            ctx.rw_events, ctx.pipe_map, ctx.sync_pairs, queue_sync_pipes=ctx.queue_sync_pipes, bar_ticks=ctx.bar_ticks
        )


class SamePipeRaceRule(RaceRule):
    def check(self, ctx):
        return detect_same_pipe_races(ctx.rw_events, ctx.pipe_map, queue_sync_pipes=ctx.queue_sync_pipes)


class GmPipelineRaceRule(RaceRule):
    def check(self, ctx):
        return detect_gm_pipeline_races(ctx.gm_events)


class GmCrossCoreRaceRule(RaceRule):
    def check(self, ctx):
        return detect_gm_cross_core_races(ctx.gm_events, ctx.cc_sync_events)


class UbCrossCoreRaceRule(RaceRule):
    def check(self, ctx):
        return detect_ub_cross_core_races(ctx.rw_events, ctx.intra_sync_events)


class ScanRaceRule(RaceRule):
    def check(self, ctx):
        return detect_scan_races(
            ctx.rw_events, ctx.pipe_map, ctx.sync_pairs, ctx.push_events, queue_sync_pipes=ctx.queue_sync_pipes
        )


class DataRaceDetector:
    """Wrap the collect → detect pipeline, running the six data-race detections in rule order."""

    # ScanRaceRule and CrossPipeRaceRule already overlap: both are cross-pipe
    # RAW/WAR/WAW with missing sync, differing only in scanline+VectorClock vs.
    # tick sweep + sync chain. CrossPipeRaceRule carries more information
    # (cur/prev PC/raw/detail), so it is kept by default; ScanRaceRule is
    # available only when added explicitly in a custom rule pipeline.
    DEFAULT_RULES = [
        CrossPipeRaceRule(),
        SamePipeRaceRule(),
        GmPipelineRaceRule(),
        GmCrossCoreRaceRule(),
        UbCrossCoreRaceRule(),
    ]

    def __init__(self, rules=None):
        self._rules = rules or self.DEFAULT_RULES

    def run(self, input_path: str) -> List[Any]:
        # Each helper DB is one kernel launch. Run detections per DB so sync state,
        # pipe maps, vector clocks and GM write/read history never leak across kernels.
        hits: List[Any] = []
        for db_file in _find_record_instr_dbs(Path(input_path)):
            ctx = self._build_context(str(db_file))
            for rule in self._rules:
                hits.extend(rule.check(ctx))
        return hits

    def _build_context(self, input_path: str) -> RaceContext:
        ctx = RaceContext()
        ctx.rw_events = collect_buffer_rw_events(input_path)
        push, set_flags, wait_flags = collect_issque_events(input_path)
        ctx.push_events = push
        ctx.pipe_map = build_pipe_map(push)
        ctx.sync_pairs = match_sync_flags(set_flags, wait_flags)
        ctx.queue_sync_pipes = collect_buf_sync_pipes(input_path)
        ctx.gm_events = collect_gm_rw_events(input_path)
        ctx.cc_sync_events = collect_cross_core_sync_events(input_path)
        ctx.intra_sync_events = collect_intra_block_sync_events(input_path)
        for sync_ev in ctx.cc_sync_events:
            if getattr(sync_ev, "kind", "") == "BAR":
                ctx.bar_ticks.setdefault(sync_ev.core, []).append(sync_ev.tick)
        for bar_list in ctx.bar_ticks.values():
            bar_list.sort()
        return ctx


def run(input_path: str) -> List[Any]:
    """Return the data-race violation list.

    Data-race violations: CROSS_PIPE_* / SAME_PIPE_* / GM_*_RACE / GM_CROSS_CORE_*
                 plus scanline+VectorClock WAW/RAW/WAR (a mix of Violation/GmViolation/RaceCandidate)."""
    return DataRaceDetector().run(input_path)


def annotate_with_source(hits: List[Any], mapper) -> List[Any]:
    """Backfill source lines for violation PCs with PcSourceMapper (modifies in place and returns).

    - Violation: a single pc (same-pipe/cross-pipe).
    - RaceCandidate: cur_pc + prev_pc (each side symbolized separately).
    - GmViolation: no device PC (record DB GM rows carry no PC), skipped.
    Returns immediately when the mapper is unavailable.
    """
    if not mapper or not getattr(mapper, "available", False):
        return hits
    by_kernel: Dict[str, List[int]] = {}
    for h in hits:
        if isinstance(h, Violation):
            if h.pc:
                by_kernel.setdefault(h.kernel_id, []).append(h.pc)
            if h.prev_pc:
                by_kernel.setdefault(h.kernel_id, []).append(h.prev_pc)
        elif isinstance(h, RaceCandidate):
            if h.cur_pc:
                by_kernel.setdefault(_core_label(h.core, h.sub, h.file), []).append(h.cur_pc)
            if h.prev_pc:
                by_kernel.setdefault(_core_label(h.core, h.sub, h.file), []).append(h.prev_pc)
    lookup = {}
    for kid, pcs in by_kernel.items():
        lookup.update(mapper.lookup_many(kid, pcs))
    for h in hits:
        if isinstance(h, Violation):
            loc = lookup.get(h.pc) if h.pc else None
            if loc:
                h.src_file, h.src_line = loc.src_file, loc.line
            loc_prev = lookup.get(h.prev_pc) if h.prev_pc else None
            if loc_prev:
                h.prev_src_file, h.prev_src_line = loc_prev.src_file, loc_prev.line
        elif isinstance(h, RaceCandidate):
            loc_cur = lookup.get(h.cur_pc) if h.cur_pc else None
            if loc_cur:
                h.cur_src_file, h.cur_src_line = loc_cur.src_file, loc_cur.line
            loc_prev = lookup.get(h.prev_pc) if h.prev_pc else None
            if loc_prev:
                h.prev_src_file, h.prev_src_line = loc_prev.src_file, loc_prev.line
    return hits


def _core_label(core: int, sub: int, file: str = "") -> str:
    """core+sub → cube/vector core name for output (no sub number printed).

    Prefer restoring the subtype from the log filename (core0.veccore0.ub → core0.vector0,
    core0.cubecore0 → core0.cube0); when the filename has no subtype, fall back to sub semantics
    (0=cubecore, 1=veccore0, 2=veccore1, consistent with _ub_subcore_id)."""
    fname = os.path.basename(file or "")
    m = re.search(r"(veccore|vec_core|cubecore)(\d+)", fname)
    if m:
        kind = "vector" if m.group(1).startswith("vec") else "cube"
        return f"core{core}.{kind}{m.group(2)}"
    if sub == 0:
        return f"core{core}.cube0"
    if sub == 1:
        return f"core{core}.vector0"
    if sub == 2:
        return f"core{core}.vector1"
    return f"core{core}.sub{sub}"


def _ub_sub_label(sub: int) -> str:
    """Subcore label for UB cross-subcore detection: 0→cube0, 1→vector0, 2→vector1."""
    if sub == 0:
        return "cube0"
    if sub == 1:
        return "vector0"
    if sub == 2:
        return "vector1"
    return f"sub{sub}"


def _ranges_str(lo_hi_pairs: List[Tuple[int, int]]) -> str:
    """Render [(lo,hi),...] as aggregated address-range text:
    range=[merged min,merged max) (distinct segment count, total count)."""
    if not lo_hi_pairs:
        return ""
    min_lo = min(p[0] for p in lo_hi_pairs)
    max_hi = max(p[1] for p in lo_hi_pairs)
    distinct = set(lo_hi_pairs)
    total = len(lo_hi_pairs)
    return f"range=[0x{min_lo:x},0x{max_hi:x}) ({len(distinct)} segments, {total} hits)"


# Rule name → description (detection domain + race type), appended after the rule
# name in output so users can tell the detection domains apart at a glance.
_RULE_DESC: Dict[str, str] = {
    # cross-pipe (no SetFlag/WaitFlag sync chain between different pipes)
    "CROSS_PIPE_RAW_NO_SYNC": "cross-pipe RAW (no sync chain)",
    "CROSS_PIPE_WAW_NO_SYNC": "cross-pipe WAW (no sync chain)",
    "CROSS_PIPE_WAR_NO_SYNC": "cross-pipe WAR (no sync chain)",
    # same-pipe (inside the instruction window, FIFO may be concurrent)
    "SAME_PIPE_RAW": "same-pipe RAW (in instruction window)",
    "SAME_PIPE_WAR": "same-pipe WAR (in instruction window)",
    "SAME_PIPE_WAW": "same-pipe WAW (in instruction window)",
    # GM same-core pipeline race
    "GM_WAW_OVERLAP": "GM same-core WAW (no intervening READ consume)",
    "GM_RAW_RACE": "GM same-core RAW",
    "GM_WAR_RACE": "GM same-core WAR",
    # GM cross-core (per-core vector clock decides happens-before)
    "GM_CROSS_CORE_WAW": "GM cross-core WAW (no cross-core sync)",
    "GM_CROSS_CORE_RAW": "GM cross-core RAW (no cross-core sync)",
    "GM_CROSS_CORE_WAR": "GM cross-core WAR (no cross-core sync)",
    # UB cross-subcore (cubecore × veccore, per-subcore vector clock)
    "UB_CROSS_CORE_WAW": "UB cross-subcore(cubecore×veccore) WAW",
    "UB_CROSS_CORE_RAW": "UB cross-subcore(cubecore×veccore) RAW",
    "UB_CROSS_CORE_WAR": "UB cross-subcore(cubecore×veccore) WAR",
}


def _rule_desc(rule_or_kind: str) -> str:
    """Rule name / scanline kind → description (detection domain + race type).
    scanline RAW/WAR/WAW are worded as "cross-pipe with no happens-before sync chain"."""
    if rule_or_kind in _RULE_DESC:
        return _RULE_DESC[rule_or_kind]
    kind_desc = {"RAW": "RAW", "WAR": "WAR", "WAW": "WAW"}
    if rule_or_kind in kind_desc:
        return f"cross-pipe {kind_desc[rule_or_kind]} (scan detection, no happens-before sync chain)"
    return ""


def _rule_label(rule_or_kind: str) -> str:
    """Rule label for output: 'rule name | description'. Returns just the rule name when there is none."""
    desc = _rule_desc(rule_or_kind)
    return f"{rule_or_kind} | {desc}" if desc else rule_or_kind


def _fmt_violation_lines(violations: List[Violation], label: str) -> List[str]:
    """Format Violation data-race records as text lines. label is used for the section title.
    Aggregates by (rule, instr_id) into one line (address ranges merged to min~max plus per-segment counts),
    instead of listing repeated violations of one instruction individually."""
    lines = []
    if violations:
        lines.append(f"Found {len(violations)} {label} violation(s):\n")
    by_kernel: Dict[str, List[Violation]] = {}
    for v in violations:
        by_kernel.setdefault(v.kernel_id, []).append(v)
    for kid, vs in by_kernel.items():
        lines.append(f"=== Kernel: {kid} ({len(vs)} violations) ===")
        # by conflict pair (rule, prev_instr_id, instr_id)
        by_key: Dict[Tuple[str, int, int], List[Violation]] = {}
        for v in vs:
            by_key.setdefault((v.rule, v.prev_instr_id, v.instr_id), []).append(v)
        for (rule, prev_iid, iid), gvs in by_key.items():
            v0 = gvs[0]
            core_label = _core_label(v0.core, v0.sub, v0.file)
            pair = (f"{prev_iid}→{iid}") if prev_iid else str(iid)
            head = "conflict instrs: " if prev_iid else "instr_id="
            base = (
                f"  [{_rule_label(rule)}] core={core_label} "
                f"pc=0x{v0.pc:x} " + head + f"{pair} op={v0.op} "
                f"pipe={v0.pipe} buf_id={v0.buf_id}"
            )
            if len(gvs) == 1:
                lines.append(base)
                lines.append(f"    {v0.detail}")
                if v0.raw:
                    lines.append(f"    raw: {v0.raw}")
                if v0.prev_raw:
                    lines.append(f"    prev raw: {v0.prev_raw}")
                if v0.prev_src_line:
                    lines.append(f"    prev src: {os.path.normpath(v0.prev_src_file)}:{v0.prev_src_line}")
                if v0.src_line:
                    lines.append(f"    src: {os.path.normpath(v0.src_file)}:{v0.src_line}")
            else:
                ranges = _ranges_str([(v.addr, v.addr + v.size) for v in gvs if v.size > 0])
                lines.append(f"{base} x{len(gvs)}")
                lines.append(f"    {v0.detail}")
                if ranges:
                    lines.append(f"    {ranges}")
                if v0.prev_raw:
                    lines.append(f"    prev raw: {v0.prev_raw}")
                if v0.prev_src_line:
                    lines.append(f"    prev src: {os.path.normpath(v0.prev_src_file)}:{v0.prev_src_line}")
                if v0.src_line:
                    lines.append(f"    src: {os.path.normpath(v0.src_file)}:{v0.src_line}")
        lines.append("")
    return lines


def _fmt_scanline_races(candidates: List[RaceCandidate]) -> List[str]:
    """Format scanline+VectorClock data races (grouped by WAW/RAW/WAR kind) as text lines.
    Aggregates by (cur_instr_id, prev_instr_id) into one line (overlap ranges merged),
    instead of listing repeated data races of one instruction pair individually."""
    lines = []
    if not candidates:
        return lines
    lines.append(f"Found {len(candidates)} scanline+VectorClock data-race(s) (overlap & IsNotHappensBefore):\n")
    by_kind: Dict[str, List[RaceCandidate]] = {}
    for c in candidates:
        by_kind.setdefault(c.kind, []).append(c)
    for kind, cs in by_kind.items():
        by_kernel: Dict[str, List[RaceCandidate]] = {}
        for c in cs:
            kid = f"{_core_label(c.core, c.sub, c.file)}.{c.buf_kind}"
            by_kernel.setdefault(kid, []).append(c)
        for kid, kcs in by_kernel.items():
            lines.append(f"=== {_rule_label(kind)}: {kid} ({len(kcs)} races) ===")
            by_pair: Dict[Tuple[int, int], List[RaceCandidate]] = {}
            for c in kcs:
                by_pair.setdefault((c.cur_instr_id, c.prev_instr_id), []).append(c)
            for (cur_iid, prev_iid), gps in by_pair.items():
                c0 = gps[0]
                n = len(gps)
                if n <= 1:
                    lines.append(
                        f"  [{_rule_label(kind)}] "
                        f"conflict instrs: {prev_iid}→{cur_iid} "
                        f"(prev {c0.prev_pipe}/{c0.prev_instr_name} "
                        f"pc=0x{c0.prev_pc:x} → cur {c0.cur_pipe}/{c0.cur_instr_name} "
                        f"pc=0x{c0.cur_pc:x}) "
                        f"overlap=[0x{c0.overlap_lo:x},0x{c0.overlap_hi:x})"
                    )
                    if c0.prev_raw:
                        lines.append(f"    prev raw: {c0.prev_raw}")
                    if c0.raw:
                        lines.append(f"    cur  raw: {c0.raw}")
                    if c0.prev_src_line:
                        lines.append(f"    prev src: {os.path.normpath(c0.prev_src_file)}:{c0.prev_src_line}")
                    if c0.cur_src_line:
                        lines.append(f"    cur  src: {os.path.normpath(c0.cur_src_file)}:{c0.cur_src_line}")
                else:
                    overlap = _ranges_str([(c.overlap_lo, c.overlap_hi) for c in gps])
                    lines.append(
                        f"  [{_rule_label(kind)}] "
                        f"conflict instrs: {prev_iid}→{cur_iid} x{n} "
                        f"(prev {c0.prev_pipe}/{c0.prev_instr_name} → "
                        f"cur {c0.cur_pipe}/{c0.cur_instr_name})"
                    )
                    if overlap:
                        lines.append(f"    {overlap}")
                    if c0.prev_src_line:
                        lines.append(f"    prev src: {os.path.normpath(c0.prev_src_file)}:{c0.prev_src_line}")
                    if c0.cur_src_line:
                        lines.append(f"    cur  src: {os.path.normpath(c0.cur_src_file)}:{c0.cur_src_line}")
            lines.append("")
    return lines


def format_text(race_hits: List[Any]) -> str:
    if not race_hits:
        return "[data_race_detection] No violations found.\n"

    pipe_hits = [h for h in race_hits if isinstance(h, Violation)]
    gm_hits = [h for h in race_hits if isinstance(h, GmViolation) and h.rule.startswith("GM_")]
    ub_xcore_hits = [h for h in race_hits if isinstance(h, GmViolation) and h.rule.startswith("UB_")]
    scan_hits = [h for h in race_hits if isinstance(h, RaceCandidate)]

    lines = [f"[data_race_detection] Found {len(race_hits)} data-race violation(s):\n"]
    lines.extend(_fmt_violation_lines(pipe_hits, "cross/same-pipe data-race"))
    lines.extend(fmt_gm_violations(gm_hits))
    lines.extend(_fmt_ub_cross_core_violations(ub_xcore_hits))
    lines.extend(_fmt_scanline_races(scan_hits))
    return "\n".join(lines) + "\n"


def _violation_json(v: Violation) -> Dict[str, Any]:
    return {
        "rule": v.rule,
        "kernel_id": v.kernel_id,
        "core": v.core,
        "core_name": _core_label(v.core, v.sub, v.file),
        "pc": v.pc,
        "instr_id": v.instr_id,
        "prev_instr_id": v.prev_instr_id,
        "op": v.op,
        "pipe": v.pipe,
        "buf_id": v.buf_id,
        "detail": v.detail,
        "severity": v.severity,
        "src_file": v.src_file,
        "src_line": v.src_line,
        "prev_pc": v.prev_pc,
        "prev_src_file": v.prev_src_file,
        "prev_src_line": v.prev_src_line,
        "prev_raw": v.prev_raw,
    }


def _candidate_json(cand: RaceCandidate) -> Dict[str, Any]:
    return {
        "core": cand.core,
        "core_name": _core_label(cand.core, cand.sub, cand.file),
        "buf_kind": cand.buf_kind,
        "kind": cand.kind,
        "cur_instr_id": cand.cur_instr_id,
        "cur_pc": cand.cur_pc,
        "cur_addr": cand.cur_addr,
        "cur_size": cand.cur_size,
        "cur_instr_name": cand.cur_instr_name,
        "cur_pipe": cand.cur_pipe,
        "prev_instr_id": cand.prev_instr_id,
        "prev_pc": cand.prev_pc,
        "prev_addr": cand.prev_addr,
        "prev_size": cand.prev_size,
        "prev_instr_name": cand.prev_instr_name,
        "prev_pipe": cand.prev_pipe,
        "overlap_lo": cand.overlap_lo,
        "overlap_hi": cand.overlap_hi,
        "is_concurrent": cand.is_concurrent,
        "cur_vt": list(cand.cur_vt),
        "prev_vt": list(cand.prev_vt),
        "cur_src_file": cand.cur_src_file,
        "cur_src_line": cand.cur_src_line,
        "prev_src_file": cand.prev_src_file,
        "prev_src_line": cand.prev_src_line,
    }


def format_json(race_hits: List[Any]) -> str:
    race_violations = []
    for h in race_hits:
        if isinstance(h, Violation):
            item = _violation_json(h)
            item["type"] = "pipe"
        elif isinstance(h, GmViolation):
            item = gm_violation_json(h)
            item["type"] = "ub_cross_core" if h.rule.startswith("UB_") else "gm"
        else:
            item = _candidate_json(h)
            item["type"] = "scanline"
        race_violations.append(item)
    return json.dumps(
        {
            "race_violations": race_violations,
        },
        indent=2,
    )


# ============================================================
# Part 4: GM (HBM) layer detection — GM event parsing + same-core/cross-core races
# ============================================================

GM_PORT_PAT = re.compile(r"^mte[0-9]+$")


@dataclass
class GmEvent:
    """GM (HBM) layer AXI event read from the helper record DB.

    One MemoryRWAccesses row with MemoryType=0 per model-side MTE transaction;
    the DB only carries user GM accesses, so no icache/dcache/vicache filtering
    is needed.
    """

    kind: str  # 'READ' / 'WRITE'
    core: int
    tick: int  # simulation tick
    port: str  # 'mte1' / 'mte2' / 'mte3' (user GM access)
    instr_id: int
    addr: int
    size: int
    file: str = ""
    line_no: int = 0
    raw: str = ""  # evidence text locates the row in reports


@dataclass
class CrossCoreSyncEvent:
    """Cross-core sync event read from the helper record DB — BAR / SET_CROSS_CORE / WAIT_FLAG_DEV(DEVI).

    Used by the cross-core vector clock to establish happens-before; accesses whose
    order is already established are not reported as races.
    """

    kind: str  # BAR / SET_CROSS_CORE / WAIT_FLAG_DEV / WAIT_FLAG_DEVI
    tick: int  # simulation cycle (instr cycle, same space as the GM access tick)
    core: int  # enclosing block/core
    flag_id: int = -1  # flag id of SET_CROSS_CORE/WAIT_FLAG_DEV
    mode: int = -1  # SET_CROSS_CORE mode (0=all-AIV barrier / 2=AIC<->AIV / 4=intra_block)


def collect_cross_core_sync_events(input_path: str) -> List[CrossCoreSyncEvent]:
    """Read the cross-core sync instructions from the helper record DB.

    Covers BAR / SET_CROSS_CORE / WAIT_FLAG_DEV(DEVI): they build the cross-core
    happens-before that clears synchronized GM accesses. SET_CROSS_CORE carries
    mode=MODE0; the other ops have no mode of their own (-1).
    """
    records = collect_record_sync_events(
        input_path,
        frozenset({"BAR", "SET_CROSS_CORE", "WAIT_FLAG_DEV", "WAIT_FLAG_DEVI"}),
    )
    return [
        CrossCoreSyncEvent(
            kind=record.op,
            tick=record.tick,
            core=record.core_id,
            flag_id=record.flag_id,
            mode=0 if record.op == "SET_CROSS_CORE" else -1,
        )
        for record in records
    ]


def collect_buf_sync_pipes(input_path: str) -> Dict[Tuple[int, int], set]:
    """Read GET_BUF / RLS_BUF from the helper record DB and derive the TQue-synced pipes.

    GET_BUF/RLS_BUF (the compiled TQue EnQue/DeQue pair) synchronize across pipes
    through a buf_id counting handshake rather than SetFlag/WaitFlag. Every pipe of
    a (core, sub) that issues them shares one queue-handshake vector time, which
    suppresses spurious RAW/WAR/WAW reports.

    Returns Dict[(core_id, sub_core_id), set(pipe_name)] with sub_core_id in the
    rw_events space (see instr_log_parser._core_type_to_rw_ids).
    """
    queue_sync_pipes: Dict[Tuple[int, int], set] = {}
    records = collect_record_sync_events(input_path, frozenset({"GET_BUF", "RLS_BUF"}))
    for record in records:
        if record.target_pipe == "?":
            continue
        core_id, sub_core_id = _core_type_to_rw_ids(record.core_id, record.core_type_id)
        queue_sync_pipes.setdefault((core_id, sub_core_id), set()).add(record.target_pipe)
    return queue_sync_pipes


@dataclass
class GmViolation:
    """GM layer violation — fields align with Violation but are reported separately from UB lock rules"""

    rule: str
    core: int
    cores: str = ""  # multiple cores involved cross-core (e.g. "0,16")
    op: str = ""
    addr: int = 0
    size: int = 0
    instr_id: int = 0
    instr_ids: str = ""  # multiple instr_ids involved cross-instr (e.g. "3,4")
    port: str = ""
    tick: int = 0
    detail: str = ""
    file: str = ""
    line_no: int = 0
    raw: str = ""  # full raw log line that triggered the violation (cur side; prev side in detail+prev_raw)
    severity: str = "error"


_GM_INSTR_ID_MASK = (1 << 32) - 1


def collect_gm_rw_events(input_path: str) -> List[GmEvent]:
    """Read the GM (HBM) read/write events from the helper record DB.

    Each MemoryRWAccesses row with MemoryType=0 is one model-side BIU MTE AXI
    transaction (a write send_wr_cmd or a read send_cmd); rows are not expanded
    per address.
    """
    gm_events: List[GmEvent] = []
    query = (
        "SELECT CoreId, AccessMode, AccessTime, AccessedAddress, "
        "Size, ExecInstrId "
        "FROM MemoryRWAccesses WHERE MemoryType = 0 "
        "ORDER BY CoreId, AccessTime, AccessedAddress"
    )
    for db_path in _find_record_instr_dbs(Path(input_path)):
        try:
            con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            logger.warning("Failed to open record DB %s: %s", db_path, exc)
            continue
        try:
            rows = con.execute(query)
            for core_id, mode, tick, addr, size, instr_id in rows:
                try:
                    core_id = int(core_id)
                    tick = int(tick)
                    addr = int(addr)
                    size = int(size) if size is not None else 0
                    instr_id = (int(instr_id) if instr_id is not None else 0) & _GM_INSTR_ID_MASK
                except (TypeError, ValueError):
                    logger.warning("Invalid GM access row in %s: CoreId=%s Addr=%s", db_path, core_id, addr)
                    continue
                gm_events.append(
                    GmEvent(
                        kind="READ" if str(mode).upper().startswith("R") else "WRITE",
                        core=core_id,
                        tick=tick,
                        port="mte0",
                        instr_id=instr_id,
                        addr=addr,
                        size=size,
                        file=str(db_path),
                        raw=(
                            f"MemoryRWAccesses MemoryType=0 CoreId={core_id} AccessMode={mode} "
                            f"AccessTime={tick} Addr=0x{addr:x} Size={size}"
                        ),
                    )
                )
        except sqlite3.Error as exc:
            logger.warning("Failed to read record DB %s: %s", db_path, exc)
        finally:
            con.close()
    return gm_events


def _gm_pipeline_violation(
    rule: str, other_kind: str, core: int, ev: GmEvent, interval: Interval, other: GmEvent, other_interval: Interval
) -> GmViolation:
    """Build a same-core GM layer pipeline race violation (WAW/RAW/WAR)."""
    inter_lo = max(interval.lo, other_interval.lo)
    inter_hi = min(interval.hi, other_interval.hi)
    return GmViolation(
        rule=rule,
        core=core,
        op=ev.kind,
        addr=inter_lo,
        size=inter_hi - inter_lo,
        instr_id=ev.instr_id,
        instr_ids=f"{other.instr_id},{ev.instr_id}",
        port=ev.port,
        tick=ev.tick,
        detail=f"{ev.kind} instr_id={ev.instr_id} port={ev.port} "
        f"range=[0x{interval.lo:x},0x{interval.hi:x}) overlaps "
        f"{other_kind} instr_id={other.instr_id} port={other.port} "
        f"range=[0x{other_interval.lo:x},0x{other_interval.hi:x}) "
        f"at tick={other.tick}→{ev.tick}, no sync between",
        file=ev.file,
        line_no=ev.line_no,
        raw=ev.raw,
    )


def _gm_write_consumed(pending_write: GmEvent, pending_reads: List[GmEvent]) -> bool:
    """Whether a pending write was consumed by some pending read (a consumed one is not WAW)."""
    pw_interval = Interval(pending_write.addr, pending_write.addr + pending_write.size)
    for r in pending_reads:
        if r.instr_id == pending_write.instr_id:
            continue
        if pw_interval.overlap(Interval(r.addr, r.addr + r.size)):
            return True
    return False


def detect_gm_pipeline_races(gm_events: List[GmEvent]) -> List[GmViolation]:
    """Rules 10/11/12: same-core GM layer pipeline races (WAW/RAW/WAR).
    Group by core and sweep the timeline once in tick order:
      - previous WRITE overlaps this WRITE at the same addr with no intervening READ consume → GM_WAW_OVERLAP
      - previous WRITE overlaps this READ at the same addr, different instr_id → GM_RAW_RACE
      - previous READ overlaps this WRITE at the same addr, different instr_id → GM_WAR_RACE
    Consecutive bursts of one instr_id (DataCopy splits) are not races."""
    hits: List[GmViolation] = []
    by_core: Dict[int, List[GmEvent]] = {}
    for ev in gm_events:
        by_core.setdefault(ev.core, []).append(ev)

    for core, events in by_core.items():
        events.sort(key=lambda e: (e.tick, e.line_no))

        def _make_violation(rule, ev, interval, other, other_interval, kind_tag):
            hits.append(_gm_pipeline_violation(rule, other.kind, core, ev, interval, other, other_interval))

        _scan_timeline(
            events,
            {"WAW": "GM_WAW_OVERLAP", "RAW": "GM_RAW_RACE", "WAR": "GM_WAR_RACE"},
            make_violation=_make_violation,
            consumed=_gm_write_consumed,
        )

    return hits


def _apply_cross_core_vc_event(
    vc: Dict[int, List[int]], idx: Dict[int, int], ev: GmEvent, vt_map: Dict[Tuple[int, int], List[int]]
) -> None:
    """GM event (block b): vc[b][b]++, recording the event's clock snapshot."""
    b = ev.core
    if b not in vc:
        return
    update_logic_time(vc[b], idx[b])
    vt_map[(ev.core, ev.instr_id)] = list(vc[b])


def _apply_cross_core_vc_barrier(vc: Dict[int, List[int]], idx: Dict[int, int], blocks: List[int]) -> None:
    """BAR global sync — delegates to vector_clock.barrier_sync."""
    barrier_sync(vc, idx, blocks)


def _apply_cross_core_vc_set_flag(
    vc: Dict[int, List[int]], ev: CrossCoreSyncEvent, signal: Dict[int, Dict[int, List[int]]]
) -> None:
    """SET_CROSS_CORE(b) — delegates to vector_clock.signal_store."""
    if ev.core not in vc:
        return
    signal_store(signal, ev.flag_id, ev.core, vc[ev.core])


def _apply_cross_core_vc_wait_flag(
    vc: Dict[int, List[int]], idx: Dict[int, int], ev: CrossCoreSyncEvent, signal: Dict[int, Dict[int, List[int]]]
) -> None:
    """WAIT_FLAG_DEV(b) — delegates to vector_clock.signal_acquire + update_logic_time."""
    if ev.core not in vc:
        return
    if signal_acquire(signal, ev.flag_id, vc[ev.core]):
        update_logic_time(vc[ev.core], idx[ev.core])
    else:
        update_logic_time(vc[ev.core], idx[ev.core])


def build_cross_core_vc(
    gm_events: List[GmEvent], sync_events: List[CrossCoreSyncEvent]
) -> Dict[Tuple[int, int], List[int]]:
    """Cross-core vector clock (block dimension, CROSS_BLOCK_CHECK semantics).

    vc[b][sb] = epoch of block sb as seen by block b. Process GM events and cross-core sync in tick order:
      - GM event (block b): vc[b][b]++
      - BAR: global sync — every block clock takes the component-wise max and then advances (≈ SyncAll)
      - SET_CROSS_CORE(b): signal[id][b] = snapshot of vc[b] (release)
      - WAIT_FLAG_DEV(b): vc[b] = max(vc[b], max_over_sets(signal[id])) then advance (acquire)
    Returns dict: (core, instr_id) → vector-clock snapshot at event time.
    No sync signals → block clocks advance independently, so cross-core events are concurrent ("assume no sync")."""
    blocks = sorted({e.core for e in gm_events})
    if not blocks:
        return {}
    n = len(blocks)
    idx = {b: i for i, b in enumerate(blocks)}
    vc: Dict[int, List[int]] = {b: [0] * n for b in blocks}
    signal: Dict[int, Dict[int, List[int]]] = {}
    vt_map: Dict[Tuple[int, int], List[int]] = {}

    timeline = [(e.tick, "GM", e) for e in gm_events]
    timeline += [(s.tick, "SYNC", s) for s in sync_events]
    timeline.sort(key=lambda t: (t[0], 0 if t[1] == "GM" else 1))

    # Only when the kernel has explicit cross-core sync (SET_CROSS_CORE/WAIT_FLAG_DEV, SyncAll's output)
    # is BAR a global cross-core barrier; otherwise it is an intra-core pipe barrier and merged clocks clear wrongly
    # unsynchronized counterexamples (e.g. the in-loop BAR of the nosync_allbar case).
    has_cross_core_sync = any(ev.kind in ("SET_CROSS_CORE", "WAIT_FLAG_DEV", "WAIT_FLAG_DEVI") for ev in sync_events)

    for _tick, tag, ev in timeline:
        if tag == "GM":
            _apply_cross_core_vc_event(vc, idx, ev, vt_map)
        elif ev.kind == "BAR" and has_cross_core_sync:
            _apply_cross_core_vc_barrier(vc, idx, blocks)
        elif ev.kind == "SET_CROSS_CORE":
            _apply_cross_core_vc_set_flag(vc, ev, signal)
        elif ev.kind in ("WAIT_FLAG_DEV", "WAIT_FLAG_DEVI"):
            _apply_cross_core_vc_wait_flag(vc, idx, ev, signal)
    return vt_map


def _cross_core_race_rule(earlier: GmEvent, later: GmEvent) -> Optional[str]:
    """Decide the cross-core race rule from the earlier/later event types; READ×READ is no race and returns None."""
    if earlier.kind == "WRITE" and later.kind == "WRITE":
        return "GM_CROSS_CORE_WAW"
    if earlier.kind == "WRITE" and later.kind == "READ":
        return "GM_CROSS_CORE_RAW"
    if earlier.kind == "READ" and later.kind == "WRITE":
        return "GM_CROSS_CORE_WAR"
    return None


def _cross_core_violation(
    earlier: GmEvent, later: GmEvent, rule: str, earlier_iv: Interval, later_iv: Interval
) -> GmViolation:
    """Build a cross-core GM race violation (severity=warning, since cross-core sync cannot be confirmed)."""
    inter_lo = max(earlier_iv.lo, later_iv.lo)
    inter_hi = min(earlier_iv.hi, later_iv.hi)
    cores = f"{earlier.core},{later.core}" if earlier.core < later.core else f"{later.core},{earlier.core}"
    return GmViolation(
        rule=rule,
        core=earlier.core,
        cores=cores,
        op=later.kind,
        addr=inter_lo,
        size=inter_hi - inter_lo,
        instr_id=earlier.instr_id,
        instr_ids=f"{earlier.instr_id},{later.instr_id}",
        port=earlier.port,
        tick=earlier.tick,
        severity="warning",
        detail=f"core{earlier.core} {earlier.kind} instr_id={earlier.instr_id} port={earlier.port} "
        f"range=[0x{earlier_iv.lo:x},0x{earlier_iv.hi:x}) at tick={earlier.tick} overlaps "
        f"core{later.core} {later.kind} instr_id={later.instr_id} port={later.port} "
        f"range=[0x{later_iv.lo:x},0x{later_iv.hi:x}) at tick={later.tick}, "
        f"no cross-core sync",
        file=earlier.file,
        line_no=earlier.line_no,
        raw=earlier.raw,
    )


def _record_cross_core_pair(
    events: List[GmEvent],
    i: int,
    j: int,
    cur_ev: GmEvent,
    cur_iv: Interval,
    best_other: Dict[Tuple[int, str], int],
    vt_map: Optional[Dict[Tuple[int, int], List[int]]],
) -> None:
    """Record overlap pair (i,j) in best_other when cross-core, non-mirror, has a rule, and unsynchronized."""
    other_ev = events[j]
    if other_ev.core == cur_ev.core:
        return  # same core is handled by the pipeline rules
    if other_ev.instr_id == cur_ev.instr_id and other_ev.tick == cur_ev.tick:
        return  # same instr and same tick is a scheduling mirror, not a race
    other_iv = Interval(other_ev.addr, other_ev.addr + other_ev.size)
    if not cur_iv.overlap(other_iv):
        return  # zero-length intervals and similar edge cases, consistent with Interval.overlap
    # decide precedence by (tick, core, line_no); earlier = earlier event, later = later event
    if (cur_ev.tick, cur_ev.core, cur_ev.line_no) < (other_ev.tick, other_ev.core, other_ev.line_no):
        rep_idx, other_idx = i, j
    elif (other_ev.tick, other_ev.core, other_ev.line_no) < (cur_ev.tick, cur_ev.core, cur_ev.line_no):
        rep_idx, other_idx = j, i
    else:
        return  # identical sort keys (theoretically unreachable), no precedence
    rule = _cross_core_race_rule(events[rep_idx], events[other_idx])
    if rule is None:
        return  # READ × READ is no data race
    # cross-core vector clock happens-before check: synchronized (happens-before) → cleared, not reported
    if vt_map is not None:
        earlier, later = events[rep_idx], events[other_idx]
        earlier_vt = vt_map.get((earlier.core, earlier.instr_id))
        later_vt = vt_map.get((later.core, later.instr_id))
        if has_happens_before_relation(earlier_vt, later_vt):
            return  # happens-before holds (cross-core sync established)
    key = (rep_idx, rule)
    if key not in best_other or other_idx < best_other[key]:
        best_other[key] = other_idx  # keep the earliest later in tick order for this (rep, rule)


def detect_gm_cross_core_races(
    gm_events: List[GmEvent], sync_events: Optional[List[CrossCoreSyncEvent]] = None
) -> List[GmViolation]:
    """Rules 13/17/18: GM_CROSS_CORE_WAW / GM_CROSS_CORE_RAW / GM_CROSS_CORE_WAR.
    Different cores accessing the same GM addr range are judged by the cross-core vector clock (block dimension):
      - WRITE × WRITE → GM_CROSS_CORE_WAW (rule 13)
      - WRITE × READ  → GM_CROSS_CORE_RAW (rule 17)
      - READ × WRITE  → GM_CROSS_CORE_WAR (rule 18)
    Cross-core sync (BAR / SET_CROSS_CORE / WAIT_FLAG_DEV) establishes happens-before → cleared, not reported;
    without sync signals block clocks advance independently, degrading to "assume no sync" (as before).

    Complexity: address sort + active-window sweep for overlap pairs, O(n log n + k) average (k = overlap pairs).
    Each rep reports each race type once to avoid duplicates; output follows rep (tick, core, line_no) order."""
    if not gm_events:
        return []
    if sync_events is None:
        sync_events = []

    # cross-core vector clock: (core, instr_id) → clock snapshot at event time (valid even with no sync signals)
    vt_map = build_cross_core_vc(gm_events, sync_events)

    # sorted by (tick, core, line_no); the index is that order — used to pick the "first match" and for output order
    events = sorted(gm_events, key=lambda e: (e.tick, e.core, e.line_no))
    n = len(events)

    # address sweep order: by interval start ascending (ties by end)
    addr_order = sorted(range(n), key=lambda i: (events[i].addr, events[i].addr + events[i].size, i))
    # active window: indices of processed events whose interval has not ended (hi > current addr);
    # their addr <= current addr and hi > current addr, so they must overlap the current event's address interval
    active: List[int] = []
    # best_other[(rep_idx, rule)] = index of the earliest matching later in tick order
    best_other: Dict[Tuple[int, str], int] = {}

    for i in addr_order:
        cur_ev = events[i]
        cur_iv = Interval(cur_ev.addr, cur_ev.addr + cur_ev.size)
        # drop ended intervals: hi <= cur_ev.addr, so they cannot overlap any later (addr >= cur_ev.addr) event
        active = [j for j in active if events[j].addr + events[j].size > cur_ev.addr]
        for j in active:
            _record_cross_core_pair(events, i, j, cur_ev, cur_iv, best_other, vt_map)
        active.append(i)

    # aggregated output: by rep tick order, and within a rep by later tick order (matching the original pairwise sweep)
    hits: List[GmViolation] = []
    by_rep: Dict[int, List[Tuple[str, int]]] = {}
    for (rep_idx, rule), other_idx in best_other.items():
        by_rep.setdefault(rep_idx, []).append((rule, other_idx))
    for rep_idx in sorted(by_rep):
        earlier = events[rep_idx]
        earlier_iv = Interval(earlier.addr, earlier.addr + earlier.size)
        for rule, j in sorted(by_rep[rep_idx], key=lambda t: t[1]):
            later = events[j]
            later_iv = Interval(later.addr, later.addr + later.size)
            hits.append(_cross_core_violation(earlier, later, rule, earlier_iv, later_iv))
    return hits


def fmt_gm_violations(gm_errors: List[GmViolation]) -> List[str]:
    """Format GM layer data-race violations as text lines.
    Aggregates by (rule, instr_id) into one line (address ranges merged to min~max plus per-segment counts)."""
    lines = []
    if gm_errors:
        lines.append(f"Found {len(gm_errors)} GM-layer violation(s):\n")
        by_rule: Dict[str, List[GmViolation]] = {}
        for v in gm_errors:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, vs in by_rule.items():
            lines.append(f"=== GM Rule: {rule} ({len(vs)} violations) ===")
            by_instr: Dict[int, List[GmViolation]] = {}
            for v in vs:
                by_instr.setdefault(v.instr_id, []).append(v)
            for iid, gvs in by_instr.items():
                v0 = gvs[0]
                core_disp = v0.cores if v0.cores else str(v0.core)
                base = (
                    f"  [{_rule_label(v0.rule)}] core={core_disp} "
                    f"op={v0.op} conflict instrs: {v0.instr_ids.replace(',', '→')} "
                    f"port={v0.port} tick={v0.tick}"
                )
                if len(gvs) == 1:
                    lines.append(base)
                    lines.append(f"    {v0.detail}")
                    if v0.raw:
                        lines.append(f"    raw: {v0.raw}")
                else:
                    ranges = _ranges_str([(v.addr, v.addr + v.size) for v in gvs])
                    lines.append(f"{base} x{len(gvs)}")
                    lines.append(f"    {v0.detail}")
                    if ranges:
                        lines.append(f"    {ranges}")
            lines.append("")
    return lines


def gm_violation_json(v: GmViolation) -> Dict[str, object]:
    return {
        "rule": v.rule,
        "core": v.core,
        "cores": v.cores,
        "op": v.op,
        "addr": v.addr,
        "size": v.size,
        "instr_id": v.instr_id,
        "instr_ids": v.instr_ids,
        "port": v.port,
        "tick": v.tick,
        "detail": v.detail,
        "severity": v.severity,
    }


def _fmt_ub_cross_core_violations(ub_hits: List[GmViolation]) -> List[str]:
    """Format UB cross-subcore data-race violations as text lines.
    Aggregates by (rule, cores, instr_ids) into one line (address ranges merged to min~max plus per-segment counts)."""
    lines: List[str] = []
    if ub_hits:
        lines.append(f"Found {len(ub_hits)} UB cross-subcore violation(s):\n")
        by_rule: Dict[str, List[GmViolation]] = {}
        for v in ub_hits:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, vs in by_rule.items():
            lines.append(f"=== UB Cross-Subcore Rule: {rule} ({len(vs)} violations) ===")
            by_key: Dict[Tuple[str, str], List[GmViolation]] = {}
            for v in vs:
                by_key.setdefault((v.cores, v.instr_ids), []).append(v)
            for (cores, iids), gvs in by_key.items():
                v0 = gvs[0]
                base = (
                    f"  [{_rule_label(v0.rule)}] core={v0.core} "
                    f"cores={cores} op={v0.op} "
                    f"conflict instrs: {iids.replace(',', '→')} tick={v0.tick}"
                )
                if len(gvs) == 1:
                    lines.append(base)
                    lines.append(f"    {v0.detail}")
                    if v0.raw:
                        lines.append(f"    raw: {v0.raw}")
                else:
                    ranges = _ranges_str([(v.addr, v.addr + v.size) for v in gvs])
                    lines.append(f"{base} x{len(gvs)}")
                    if ranges:
                        lines.append(f"    {ranges}")
            lines.append("")
    return lines


# ============================================================
# Part 5: UB cross-subcore detection (Ascend950: AICube + AIVector share UB)
# ============================================================


@dataclass
class IntraBlockSyncEvent:
    """Intra-core block sync event — SET_INTRA_BLOCK / WAIT_INTRA_BLOCK.
    Provides happens-before for the UB cross-subcore clock, clearing synchronized cubecore↔veccore UB accesses."""

    kind: str  # SET_INTRA_BLOCK / SET_INTRA_BLOCKI / WAIT_INTRA_BLOCK / WAIT_INTRA_BLOCKI
    tick: int  # simulation cycle (instr cycle, same space as UB access ticks)
    core: int  # enclosing block/core
    sub: int  # 0=cubecore, 1=veccore0, 2=veccore1
    flag_id: int  # block_id (used to match SET/WAIT)


def collect_intra_block_sync_events(input_path: str) -> List[IntraBlockSyncEvent]:
    """Read SET_INTRA_BLOCK / WAIT_INTRA_BLOCK instructions from the helper record DB.

    (core, sub) comes from the DB core coordinates and stays in the subcore space
    used by _ub_subcore_id (0=cubecore, 1=veccore0, 2=veccore1).
    """
    records = collect_record_sync_events(
        input_path,
        frozenset(
            {
                "SET_INTRA_BLOCK",
                "SET_INTRA_BLOCKI",
                "WAIT_INTRA_BLOCK",
                "WAIT_INTRA_BLOCKI",
            }
        ),
    )
    events: List[IntraBlockSyncEvent] = []
    for record in records:
        core_id, sub_id = _core_type_to_ids(record.core_id, record.core_type_id)
        events.append(
            IntraBlockSyncEvent(
                kind=record.op,
                tick=record.tick,
                core=core_id,
                sub=sub_id,
                flag_id=record.flag_id,
            )
        )

    events.sort(key=lambda e: (e.core, e.tick))
    return events


def build_ub_cross_core_vc(
    ub_events: List[RwEvent], sync_events: List[IntraBlockSyncEvent]
) -> Dict[Tuple[int, int, int], List[int]]:
    """UB cross-subcore vector clock (intra-block subcore dimension).

    Each block keeps its own VC set, sized by the subcores seen in that block (usually 2-3: cubecore/veccore0/veccore1).
    Process UB events and intra-block sync in tick order:
      - UB event (sub s): vc[s][s_idx]++, recording the event's clock snapshot
      - SET_INTRA_BLOCK(sub s, flag_id): signal[flag_id][s] = snapshot of vc[s] (release)
      - WAIT_INTRA_BLOCK(sub s, flag_id): vc[s] = max(vc[s], max_over_sets(signal[flag_id])) then advance (acquire)
    Returns dict: (core, sub, instr_id) → vector-clock snapshot at event time.
    No sync signals → independent subcore clocks, so cross-subcore events are always concurrent (conservative).

    Note: RwEvent.sub is the index after the subtype (cubecore0→0, veccore0→0) and cannot distinguish cubecore/veccore.
    Here _ub_subcore_id infers a unique subcore ID from the filename (0=cubecore, 1=veccore0, 2=veccore1)."""
    vt_map: Dict[Tuple[int, int, int], List[int]] = {}

    by_block: Dict[int, List[Any]] = {}
    for ev in ub_events:
        by_block.setdefault(ev.core, []).append(ev)
    for ev in sync_events:
        by_block.setdefault(ev.core, []).append(ev)

    for block_id, items in by_block.items():
        subs = sorted({_ub_subcore_id(ev) if isinstance(ev, RwEvent) else ev.sub for ev in items})
        if len(subs) < 2:
            continue
        idx = {s: i for i, s in enumerate(subs)}
        n = len(subs)
        vc: Dict[int, List[int]] = {s: [0] * n for s in subs}
        signal: Dict[int, Dict[int, List[int]]] = {}

        timeline: List[Tuple[int, str, Any]] = []
        for ev in ub_events:
            if ev.core == block_id:
                timeline.append((ev.tick, "UB", ev))
        for ev in sync_events:
            if ev.core == block_id:
                timeline.append((ev.tick, "SYNC", ev))
        timeline.sort(key=lambda t: (t[0], 0 if t[1] == "UB" else 1))

        for _tick, tag, ev in timeline:
            if tag == "UB":
                s = _ub_subcore_id(ev)
                if s not in vc:
                    continue
                update_logic_time(vc[s], idx[s])
                vt_map[(ev.core, s, ev.instr_id)] = list(vc[s])
            elif isinstance(ev, IntraBlockSyncEvent):
                s = ev.sub
                if s not in vc:
                    continue
                if ev.kind.startswith("SET"):
                    signal_store(signal, ev.flag_id, s, vc[s])
                else:  # WAIT
                    signal_acquire(signal, ev.flag_id, vc[s])
                    update_logic_time(vc[s], idx[s])
    return vt_map


def _ub_cross_core_race_rule(earlier: RwEvent, later: RwEvent) -> Optional[str]:
    if earlier.kind == "WRITE" and later.kind == "WRITE":
        return "UB_CROSS_CORE_WAW"
    if earlier.kind == "WRITE" and later.kind == "READ":
        return "UB_CROSS_CORE_RAW"
    if earlier.kind == "READ" and later.kind == "WRITE":
        return "UB_CROSS_CORE_WAR"
    return None


def _ub_subcore_id(ev: RwEvent) -> int:
    """Infer a unique subcore ID (cubecore→0, veccore0→1, veccore1→2).
    RwEvent.sub is only the index after the subtype (cubecore0→0, veccore0→0) and cannot distinguish cubecore/veccore.
    DB events carry CoreTypeId (1=cube, 2=vector0, 3=vector1), which maps directly;
    other events fall back to the subtype encoded in the source file name.
    The result is cached on the event object (immutable per event)."""
    cached = getattr(ev, "_ub_subcore_id", None)
    if cached is not None:
        return cached
    fname = os.path.basename(ev.file)
    if "cubecore" in fname:
        result = 0
    elif "veccore1" in fname or "vec_core1" in fname:
        result = 2
    elif "veccore" in fname or "vec_core" in fname:
        result = 1
    elif ev.core_type_id == 1:  # _CORE_TYPE_CUBE
        result = 0
    elif ev.core_type_id == 3:  # _CORE_TYPE_VECTOR1
        result = 2
    elif ev.core_type_id == 2:  # _CORE_TYPE_VECTOR0
        result = 1
    else:
        result = ev.sub
    try:
        ev._ub_subcore_id = result  # RwEvent is a dataclass, so extra attributes are allowed
    except Exception:
        pass
    return result


# Cube fixpipe → UB write instruction set
# FIX_L0C_TO_DST is issued by cubecore's fixp queue and writes UB; the simulator records this one fixpipe
# write into both veccore0 and veccore1 of the same core (same instr_id, tick, PC, addr).
# This mirrors one physical write, not two independent writes. Grouping by subcore makes the detector treat it
# as coming from two subcores, producing false WAW/RAW/WAR against veccore's own UB accesses, so it must be skipped.
CUBE_FIXPIPE_UB_WRITE_NAMES = frozenset(
    {
        "FIX_L0C_TO_DST",
    }
)


def _is_cube_fixpipe_ub_write(ev: RwEvent) -> bool:
    """Decide whether a UB write event is a cubecore fixpipe write (e.g. FIX_L0C_TO_DST).

    The simulator records the same cubecore fixpipe write event into both veccores
    (same instr_id and tick). Grouping by subcore would treat it
    as a veccore write and produce false cross-subcore races against veccore's own UB accesses, so it is skipped."""
    return ev.kind == "WRITE" and ev.instr_name in CUBE_FIXPIPE_UB_WRITE_NAMES


def _build_veccore_independent_ub_pairs(ub_events: List[RwEvent]) -> Dict[Tuple[int, int, int], bool]:
    """Identify the pair of veccore subcores within one core (each with its own UB and write port).

    In Ascend mix-mode, veccore0 and veccore1 within a core each own an independent UB memory
    and write port; both use the same local addresses for their own UB, so no cross-core race physically exists.
    Grouping veccore0/veccore1 into different subcores by filename reports "same addresses in two independent UBs"
    as false cross-subcore WAW/RAW/WAR. This function identifies the (veccore0, veccore1) pair within a core
    so the detector can skip all cross-subcore UB races between them.

    Returns dict: (core, sub1, sub2) → True means (sub1, sub2) are both veccore subcores of the same core
    (independent UBs), and cross-subcore UB races between them must be skipped."""
    by_core_sub: Dict[Tuple[int, int], set] = {}
    for ev in ub_events:
        key = (ev.core, _ub_subcore_id(ev))
        s = by_core_sub.setdefault(key, set())
        s.add((ev.instr_name, ev.pc))

    pairs: Dict[Tuple[int, int, int], bool] = {}
    cores = sorted({c for c, _ in by_core_sub})
    for c in cores:
        # _ub_subcore_id: 1=veccore0, 2=veccore1 (0=cubecore, which does not write UB directly)
        vec_subs = sorted({s for cc, s in by_core_sub if cc == c and s in (1, 2)})
        for i in range(len(vec_subs)):
            for j in range(i + 1, len(vec_subs)):
                si, sj = vec_subs[i], vec_subs[j]
                pairs[(c, si, sj)] = True
                pairs[(c, sj, si)] = True
    return pairs


def _is_independent_ub_pair(core: int, sub_a: int, sub_b: int, pairs: Dict[Tuple[int, int, int], bool]) -> bool:
    """Check whether (core, sub_a, sub_b) are two veccore subcores of the same core (independent UBs)."""
    return pairs.get((core, sub_a, sub_b), False)


def detect_ub_cross_core_races(
    rw_events: List[RwEvent], sync_events: Optional[List[IntraBlockSyncEvent]] = None
) -> List[GmViolation]:
    """Rules 22/23/24: UB_CROSS_CORE_WAW / UB_CROSS_CORE_RAW / UB_CROSS_CORE_WAR.

    Different subcores in one block (cubecore vs veccore) accessing the same UB address interval are
    judged by a per-subcore vector clock (SET_INTRA_BLOCK / WAIT_INTRA_BLOCK):
      - WRITE × WRITE → UB_CROSS_CORE_WAW (rule 22)
      - WRITE × READ  → UB_CROSS_CORE_RAW (rule 23)
      - READ × WRITE  → UB_CROSS_CORE_WAR (rule 24)
    Intra-block sync present → cleared, not reported; absent → reported (severity=error).

    Complexity: address sort + active-window scan, O(n log n + k)."""
    ub_events = [e for e in rw_events if e.buf_kind == "ub" and not _is_cube_fixpipe_ub_write(e)]
    if not ub_events:
        return []
    if sync_events is None:
        sync_events = []

    vt_map = build_ub_cross_core_vc(ub_events, sync_events)

    # precompute same-core (veccore0, veccore1) pairs — each owns an independent UB memory and write port,
    # and same local addresses hit each one's own UB, so no cross-core race exists physically. Grouping by filename
    # would report "same-address accesses to two independent UBs" as cross-subcore WAW/RAW/WAR, so skip them entirely.
    independent_ub_pairs = _build_veccore_independent_ub_pairs(ub_events)

    events = sorted(ub_events, key=lambda e: (e.core, e.tick, _ub_subcore_id(e), e.line_no))

    by_block: Dict[int, List[int]] = {}
    for i, ev in enumerate(events):
        by_block.setdefault(ev.core, []).append(i)

    hits: List[GmViolation] = []
    for block_id, indices in by_block.items():
        block_events = [events[i] for i in indices]
        if len({_ub_subcore_id(e) for e in block_events}) < 2:
            continue

        addr_order = sorted(
            range(len(block_events)),
            key=lambda i: (block_events[i].addr, block_events[i].addr + block_events[i].size, i),
        )
        active: List[int] = []
        reported: Dict[Tuple[int, int, str], bool] = {}

        for i in addr_order:
            cur_ev = block_events[i]
            cur_sub = _ub_subcore_id(cur_ev)
            cur_iv = Interval(cur_ev.addr, cur_ev.addr + cur_ev.size)
            active = [j for j in active if block_events[j].addr + block_events[j].size > cur_ev.addr]
            for j in active:
                other_ev = block_events[j]
                other_sub = _ub_subcore_id(other_ev)
                if other_sub == cur_sub:
                    continue
                # same instr_id + same tick is a mirror write of one instruction (broadcasts such as SET_INTRA_BLOCKI
                # trigger UB writes on all subcores; or a cube fixpipe write recorded by the simulator into both
                # veccores), so it is not a cross-subcore data race
                if other_ev.instr_id == cur_ev.instr_id and other_ev.tick == cur_ev.tick:
                    continue
                # same-core veccore0/veccore1 have independent UBs and same local addresses hit each one's own UB,
                # so no cross-core race physically exists; skip
                if _is_independent_ub_pair(block_id, cur_sub, other_sub, independent_ub_pairs):
                    continue
                other_iv = Interval(other_ev.addr, other_ev.addr + other_ev.size)
                if not cur_iv.overlap(other_iv):
                    continue
                if (cur_ev.tick, cur_sub, cur_ev.line_no) < (other_ev.tick, other_sub, other_ev.line_no):
                    earlier, later = cur_ev, other_ev
                    earlier_sub, later_sub = cur_sub, other_sub
                else:
                    earlier, later = other_ev, cur_ev
                    earlier_sub, later_sub = other_sub, cur_sub
                rule = _ub_cross_core_race_rule(earlier, later)
                if rule is None:
                    continue
                earlier_vt = vt_map.get((earlier.core, earlier_sub, earlier.instr_id))
                later_vt = vt_map.get((later.core, later_sub, later.instr_id))
                if has_happens_before_relation(earlier_vt, later_vt):
                    continue
                rep_key = (i, rule)
                if rep_key in reported:
                    continue
                reported[rep_key] = True
                inter_lo = max(earlier.addr, later.addr)
                inter_hi = min(earlier.addr + earlier.size, later.addr + later.size)
                hits.append(
                    GmViolation(
                        rule=rule,
                        core=block_id,
                        cores=f"{_ub_sub_label(earlier_sub)},{_ub_sub_label(later_sub)}",
                        op=later.kind,
                        addr=inter_lo,
                        size=inter_hi - inter_lo,
                        instr_id=earlier.instr_id,
                        instr_ids=f"{earlier.instr_id},{later.instr_id}",
                        port=f"{_ub_sub_label(earlier_sub)}/{earlier.instr_name}",
                        tick=earlier.tick,
                        severity="error",
                        detail=(
                            f"core{block_id} {_ub_sub_label(earlier_sub)} {earlier.kind} "
                            f"instr_id={earlier.instr_id} "
                            f"range=[0x{earlier.addr:x},0x{earlier.addr + earlier.size:x}) tick={earlier.tick} "
                            f"overlaps {_ub_sub_label(later_sub)} {later.kind} instr_id={later.instr_id} "
                            f"range=[0x{later.addr:x},0x{later.addr + later.size:x}) tick={later.tick}, "
                            f"no SET_INTRA_BLOCK/WAIT_INTRA_BLOCK sync"
                        ),
                        file=earlier.file,
                        line_no=earlier.line_no,
                        raw=earlier.raw,
                    )
                )
            active.append(i)

    return hits
