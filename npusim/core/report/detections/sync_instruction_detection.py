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

"""Sync instruction validity detection.

Instruction events are read from the helper record DB; other logs use text input.

Supported instructions: SET_FLAG / SET_FLAGI / WAIT_FLAG / WAIT_FLAGI / HSET_FLAG / HWAIT_FLAG /
          SET_CROSS_CORE / WAIT_FLAG_DEV / WAIT_FLAG_DEVI / SET_INTRA_BLOCK / SET_INTRA_BLOCKI /
          WAIT_INTRA_BLOCK / WAIT_INTRA_BLOCKI / BAR / GET_BUF / RLS_BUF / NOP_PIPE / SYNC_ALL

Rules:
  1.  FLAG_ID_OOB              — flag_id >= 16
  2.  REDUNDANT_SET_FLAG / REDUNDANT_WAIT_FLAG - two consecutive SET_FLAG / WAIT_FLAG operations
      on the same pipe have identical parameters (redundancy detection, see Part 2B)
  3.  DUP_SET_UNCONSUMED       - a new SET arrives before the previous SET is consumed for the same
      (pipe, trig_pipe, flag_id)
  4a. WAIT_NO_ARRIVE           - WAIT_FLAG has no preceding SET_FLAG
  4c. WAIT_OVERCONSUME         - WAIT count is greater than SET count (also matches mismatched pipes)
  4d. UNCONSUMED_ARRIVE        - SET count is greater than WAIT count at kernel end
  4e. BLOCK_WAIT_PREMATURE     - WAIT_INTRA_BLOCK sees counter=0
  5.  SYNCALL_COUNT_MISMATCH   - SyncAll() call counts differ across cores (deadlock risk)

  FFTS cross-core sync mode dispatch:
  SET_CROSS_CORE -> MODE0 (network-wide broadcast to cores of the same kind)
  SET_INTRA_BLOCK -> MODE4 (two execution-core kinds in a block wait for each other)

  mode is inferred from the op:
    SET_CROSS_CORE/WAIT_FLAG_DEV → mode=0
    SET/WAIT_INTRA_BLOCK → mode=4

  MODE4 pairing topology (vecSubBlockDim=2):
    VEC0 SET(id=N) → CUBE WAIT(N)
    VEC1 SET(id=N) → CUBE WAIT(N+16)
    CUBE SET(id=N, 0-15) → VEC0 WAIT(N)
    CUBE SET(id=N, 16-31) → VEC1 WAIT(N-16)

  6a. MIX_MODE_ID_OOB_VEC      - VECCORE SET/WAIT id > 15
  6b. MIX_MODE_ID_OOB_CUBE     - CUBECORE SET/WAIT id > 31
  6d. MIX_MODE_UNCONSUMED      - SET is not consumed by peer WAIT at kernel end
  6e. MIX_MODE_WAIT_NO_ARRIVE  - WAIT has no preceding SET for the corresponding sync_id

Limitation: soft sync cannot be detected; MODE1/MODE2 topologies are reserved for their instructions.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Tuple, Optional

from npusim.core.report.detections.instr_log_parser import (
    RecordInstruction,
    _core_type_to_core_name,
    _SYNC_OPS,
    collect_record_instructions,
)
from npusim.core.report.detections.log_patterns import CORE_KIND_PAT

logger = logging.getLogger(__name__)

# GET_BUF/RLS_BUF (asc_lock) protocol constants
MAX_BUF_ID = 16


# FFTS cross-core sync modes:
#   MODE0 network-wide broadcast
#   MODE1 two vector cores in a group wait for each other
#   MODE2 vector core and cube core wait for each other
#   MODE3 reserved
#   MODE4 vector core and cube core in a block wait for each other
class FftsSyncMode:
    MODE0 = 0
    MODE1 = 1
    MODE2 = 2
    MODE3 = 3
    MODE4 = 4


# Mix mode: one cube core plus two vector cores form a group.
VEC_SUB_BLOCKDIM = 2
MIX_SUB_BLOCKDIM = 3
MAX_VEC_SYNC_ID = 16  # VECCORE side 0-15


def _core_kind_to_block_idx(kind: str) -> int:
    """Map core kind to group index: VEC0->0, VEC1->1, CUBE->2."""
    if kind == "VEC0":
        return 0
    if kind == "VEC1":
        return 1
    if kind == "CUBE":
        return VEC_SUB_BLOCKDIM  # = 2
    return -1


def _block_idx_to_core_kind(idx: int) -> str:
    """Map group index to core kind."""
    if idx == 0:
        return "VEC0"
    if idx == 1:
        return "VEC1"
    if idx == VEC_SUB_BLOCKDIM:
        return "CUBE"
    return "UNKNOWN"


@dataclass
class SyncEvent:
    op: str
    core: str
    pc: int
    instr_id: int
    cycle: int
    pipe: str = "?"
    trig_pipe: str = "?"
    flag_id: int = 0
    file: str = ""
    line_no: int = 0
    raw: str = ""
    header_pipe: str = ""
    rls_count: int = 0
    ffts_mode: int = -1
    vec_sub_block_dim: int = 2
    src_core: str = ""
    dst_core: str = ""
    mix_id: int = -1


def core_kind(core_name: str) -> str:
    """Infer core kind from a core name: CUBE / VEC0 / VEC1."""
    lower = core_name.lower()
    if "cube" in lower:
        return "CUBE"
    match = CORE_KIND_PAT.search(core_name)
    if match:
        return f"VEC{match.group(1)}"
    return "UNKNOWN"


def _infer_ffts_mode(op: str) -> int:
    """Infer the FFTS sync mode from the instruction type.

    SET_CROSS_CORE / WAIT_FLAG_DEV / WAIT_FLAG_DEVI are MODE0;
    SET/WAIT_INTRA_BLOCK are MODE4; other instructions return -1.
    """
    if op in ("SET_CROSS_CORE", "WAIT_FLAG_DEV", "WAIT_FLAG_DEVI"):
        return FftsSyncMode.MODE0
    if op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI", "WAIT_INTRA_BLOCK", "WAIT_INTRA_BLOCKI"):
        return FftsSyncMode.MODE4
    return -1


def _record_instruction_to_sync_event(record: RecordInstruction) -> SyncEvent:
    """Convert record DB instruction records to detection events."""
    return SyncEvent(
        op=record.op,
        core=_core_type_to_core_name(record.core_id, record.core_type_id),
        pc=record.pc,
        instr_id=record.instr_id,
        cycle=record.tick,
        pipe=record.target_pipe,
        trig_pipe=record.consumer_pipe,
        flag_id=record.flag_id,
        file="",
        line_no=0,
        raw=f"ExecInstrId={record.instr_id} op={record.op} tick={record.tick}",
        ffts_mode=_infer_ffts_mode(record.op),
    )


SYNC_OPS = _SYNC_OPS


@dataclass
class InstrEvent:
    """Instruction stream events used by redundant sync detection."""

    op: str
    core: str
    pc: int
    instr_id: int
    cycle: int
    header_pipe: str
    is_sync: bool
    pipe: str = ""
    trig_pipe: str = ""
    flag_id: int = 0
    file: str = ""
    line_no: int = 0
    raw: str = ""


def _record_instruction_to_instr_event(record: RecordInstruction) -> InstrEvent:
    """Convert record DB instruction records to redundancy detection events."""
    return InstrEvent(
        op=record.op,
        core=_core_type_to_core_name(record.core_id, record.core_type_id),
        pc=record.pc,
        instr_id=record.instr_id,
        cycle=record.tick,
        header_pipe=record.target_pipe,
        is_sync=record.is_sync,
        pipe=record.target_pipe,
        trig_pipe=record.consumer_pipe,
        flag_id=record.flag_id,
        raw=f"ExecInstrId={record.instr_id} op={record.op} tick={record.tick}",
    )


def precompute_rls_count(events: List[SyncEvent]) -> None:
    """Count RLS_BUF operations by (core, buf_id) and fill GET_BUF events."""
    rls_map: Dict[Tuple[str, int], int] = {}
    for ev in events:
        if ev.op in ("GET_BUF", "RLS_BUF"):
            key = (ev.core, ev.flag_id)
            if ev.op == "RLS_BUF":
                rls_map[key] = rls_map.get(key, 0) + 1
    # fill rls_count into GET_BUF events
    for ev in events:
        if ev.op == "GET_BUF":
            ev.rls_count = rls_map.get((ev.core, ev.flag_id), 0)


def collect_events(input_path: str) -> List[SyncEvent]:
    """Read sync instructions from the helper record DB and sort them."""
    records = collect_record_instructions(input_path)
    events = [_record_instruction_to_sync_event(record) for record in records if record.is_sync]
    events.sort(key=lambda event: (event.cycle, event.instr_id))
    return events


def collect_instr_stream(input_path: str) -> List[InstrEvent]:
    """Read the instruction stream required by redundancy detection from the helper record DB."""
    events = [_record_instruction_to_instr_event(record) for record in collect_record_instructions(input_path)]
    events.sort(key=lambda event: (event.cycle, event.instr_id))
    return events


# ============================================================
# Part 2: state tracking and detection rules
# ============================================================


@dataclass
class EventPoolState:
    set_count: int = 0
    wait_count: int = 0
    last_set_pc: int = 0
    last_set_id: int = 0


@dataclass
class CrossCoreState:
    counter: int = 0
    set_count: int = 0
    wait_count: int = 0


@dataclass
class BufSlotState:
    """GET_BUF/RLS_BUF counting state keyed by (core, buf_id)."""

    consumed: int = 0  # number of consumed GET_BUF operations
    rls_queue: List = field(default_factory=list)  # FIFO queue of RLS_BUF events


@dataclass
class BlockSyncState:
    counter: int = 0
    set_count: int = 0
    wait_count: int = 0
    arrived_pcs: List[int] = field(default_factory=list)


# FFTS cross-core sync container.


@dataclass
class BlockSyncInfo:
    """Sync FIFO queues keyed by (block_idx, flag_id)."""

    setVec0: List = field(default_factory=list)  # MODE0: network-wide broadcast sets
    setVec1: List = field(default_factory=list)  # MODE1 set
    setVec2: List = field(default_factory=list)  # MODE2 set
    setVec4: List = field(default_factory=list)  # MODE4: intra-block set
    waitVec: List = field(default_factory=list)  # signals waiting for WAIT-side consumption
    intrablockwaitVec: List = field(default_factory=list)  # MODE4 WAIT side


class CrossCoreSyncContainer:
    """FFTS cross-core sync information container.

    Pairing topology dispatched by mode:
      MODE0: network-wide broadcast
      MODE1: two vector cores in a group wait for each other
      MODE2: vector core and cube core wait for each other
      MODE4: vector core and cube core in a block wait for each other

    Core mapping: core_kind(VEC0/VEC1/CUBE) -> logical blockIdx(0/1/2) in a group.
    Group number is derived from core cid: cid//16 -> group (cid=0 -> group0, cid=16 -> group1, ...).
    """

    def __init__(self):
        self._block_sync: Dict[Tuple[int, int], Dict[int, BlockSyncInfo]] = {}

    def _get_info(self, group: int, idx: int, flag_id: int) -> BlockSyncInfo:
        """Get or create BlockSyncInfo for (group, idx, flag_id)."""
        return self._block_sync.setdefault((group, idx), {}).setdefault(flag_id, BlockSyncInfo())

    @staticmethod
    def _core_location(core_name: str) -> Tuple[int, int, str]:
        """Resolve (group_id, block_idx, core_kind) from a core name."""
        kind = core_kind(core_name)
        idx = _core_kind_to_block_idx(kind)
        prefix = core_name.split(".", 1)[0]
        try:
            cid = int(prefix[len("core") :]) if prefix.startswith("core") else 0
        except ValueError:
            cid = 0
        return cid, idx, kind

    def set_block_sync_info(
        self, flag_id: int, mode: int, core_name: str, ev: "SyncEvent", vec_sub_block_dim: int = 2
    ) -> None:
        """Dispatch a sync event by mode."""
        if mode == FftsSyncMode.MODE0:
            self._set_mode0(flag_id, core_name, ev)
        elif mode == FftsSyncMode.MODE1:
            self._set_mode1(flag_id, core_name, ev)
        elif mode == FftsSyncMode.MODE2:
            self._set_mode2(flag_id, core_name, ev, vec_sub_block_dim)
        elif mode == FftsSyncMode.MODE4:
            self._set_mode4(flag_id, core_name, ev, vec_sub_block_dim)
        # MODE3 is reserved.

    def get_block_sync_info(self, flag_id: int, core_name: str) -> Optional["SyncEvent"]:
        """WAIT_FLAG_DEV consumes one pending signal."""
        group, idx, _ = self._core_location(core_name)
        info = self._block_sync.get((group, idx), {}).get(flag_id)
        if info and info.waitVec:
            return info.waitVec.pop(0)
        return None

    def get_intra_block_sync_info(self, sync_id: int, core_name: str) -> Optional["SyncEvent"]:
        """WAIT_INTRA_BLOCK consumes one intra-block sync signal."""
        group, idx, kind = self._core_location(core_name)
        if kind != "CUBE" and sync_id >= MAX_VEC_SYNC_ID:
            sync_id = sync_id & 0xF
        info = self._block_sync.get((group, idx), {}).get(sync_id)
        if info and info.intrablockwaitVec:
            return info.intrablockwaitVec.pop(0)
        return None

    # ---- MODE0: network-wide broadcast ----
    def _set_mode0(self, flag_id: int, core_name: str, ev: "SyncEvent") -> None:
        """Record a MODE0 signal and dispatch it to cores of the same kind."""
        group, idx, kind = self._core_location(core_name)
        info = self._get_info(group, idx, flag_id)
        # VEC0 是 MODE0 广播的发起位：它保存原始 SET 用于 kernel-end 漏检，
        # 并立即把信号路由到同组两个 VEC 的 WAIT 队列。
        if kind == "CUBE":
            info.setVec0.append(ev)
            return

        if idx != 0:
            # 非 VEC0 发出的 MODE0 SET 不是当前实现的广播源；保留在自身队列
            # 供 pending_sets 检查，避免重复作为同一事件的 WAIT 目标。
            info.setVec0.append(ev)
            return

        info.setVec0.append(ev)
        wait_infos = [self._get_info(group, wait_idx, flag_id) for wait_idx in range(VEC_SUB_BLOCKDIM)]
        for wait_info in wait_infos:
            wait_info.waitVec.append(ev)

    # ---- MODE1: two vector cores in a group wait for each other ----
    def _set_mode1(self, flag_id: int, core_name: str, ev: "SyncEvent") -> None:
        """Record a MODE1 signal and pair it between the two vector cores."""
        group, idx, kind = self._core_location(core_name)
        if kind == "CUBE":
            return
        peer_idx = 1 - idx if idx in (0, 1) else idx
        peer_info = self._get_info(group, peer_idx, flag_id)
        if peer_info.setVec1:
            blv = peer_info.setVec1.pop(0)
            self._get_info(group, idx, flag_id).waitVec.append(blv)
            peer_info.waitVec.append(blv)
            return
        self._get_info(group, idx, flag_id).setVec1.append(ev)

    # ---- MODE2: vector core and cube core wait for each other ----
    def _set_mode2(self, flag_id: int, core_name: str, ev: "SyncEvent", vec_sub_block_dim: int) -> None:
        """Record a MODE2 signal according to the vector-core ratio."""
        if vec_sub_block_dim == 0:
            return
        group, idx, kind = self._core_location(core_name)
        info = self._get_info(group, idx, flag_id)
        info.setVec2.append(ev)

        if vec_sub_block_dim == 1:
            if kind == "CUBE":
                aiv_idx = idx // MIX_SUB_BLOCKDIM
                self._get_info(group, aiv_idx, flag_id).waitVec.append(ev)
                info.setVec2.pop()
            else:
                aic_idx = VEC_SUB_BLOCKDIM
                self._get_info(group, aic_idx, flag_id).waitVec.append(ev)
                info.setVec2.pop()
        else:
            if kind == "CUBE":
                self._get_info(group, idx - 2, flag_id).waitVec.append(ev)
                self._get_info(group, idx - 1, flag_id).waitVec.append(ev)
                info.setVec2.pop()
            else:
                peer_idx = 1 - idx if idx in (0, 1) else idx
                peer_info = self._get_info(group, peer_idx, flag_id)
                if not peer_info.setVec2:
                    return
                vt = peer_info.setVec2.pop(0)
                aic_idx = VEC_SUB_BLOCKDIM
                self._get_info(group, aic_idx, flag_id).waitVec.append(vt)
                info.setVec2.pop()

    # ---- MODE4: vector core and cube core in a block wait for each other ----
    def _set_mode4(self, sync_id: int, core_name: str, ev: "SyncEvent", vec_sub_block_dim: int) -> None:
        """Record a MODE4 signal according to the vector-core ratio."""
        if vec_sub_block_dim == 0:
            return
        group, idx, kind = self._core_location(core_name)
        if kind != "CUBE" and sync_id >= MAX_VEC_SYNC_ID:
            sync_id = sync_id & 0xF
        info = self._get_info(group, idx, sync_id)
        info.setVec4.append(ev)

        if vec_sub_block_dim == 1:
            if kind == "CUBE":
                aiv_idx = idx // MIX_SUB_BLOCKDIM
                self._get_info(group, aiv_idx, sync_id).intrablockwaitVec.append(ev)
            else:
                aic_idx = VEC_SUB_BLOCKDIM
                self._get_info(group, aic_idx, sync_id).intrablockwaitVec.append(ev)
            info.setVec4.pop()
        else:
            if kind == "CUBE":
                if sync_id <= 15:
                    self._get_info(group, idx - 2, sync_id).intrablockwaitVec.append(ev)
                elif 16 <= sync_id <= 31:
                    self._get_info(group, idx - 1, sync_id - 16).intrablockwaitVec.append(ev)
                info.setVec4.pop()
            else:
                aic_idx = VEC_SUB_BLOCKDIM
                if idx == 0:  # VEC0
                    self._get_info(group, aic_idx, sync_id).intrablockwaitVec.append(ev)
                else:  # VEC1
                    self._get_info(group, aic_idx, sync_id + 16).intrablockwaitVec.append(ev)
                info.setVec4.pop()

    def process_intra_block_event(self, kernel: "KernelScope", ev: "SyncEvent") -> List["Violation"]:
        """Process intra-block sync events and return violations."""
        kernel.mix_mode_enabled = True
        kind = core_kind(ev.core)
        sid = ev.flag_id
        # 6a/6b: INTRA_BLOCK id bounds check
        if kind in ("VEC0", "VEC1") and sid > 15:
            return [
                _sync_violation(
                    "MIX_MODE_ID_OOB_VEC", kernel.kernel_id, ev, f"src_core={kind} src_id={sid} > 15 (VECCORE id limit)"
                )
            ]
        if kind == "CUBE" and sid > 31:
            return [
                _sync_violation(
                    "MIX_MODE_ID_OOB_CUBE",
                    kernel.kernel_id,
                    ev,
                    f"src_core={kind} src_id={sid} > 31 (CUBECORE id limit)",
                )
            ]
        is_set = ev.op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI")
        if is_set:
            # SET: push into container; _set_mode4 routes to peer intrablockwaitVec
            self.set_block_sync_info(ev.flag_id, FftsSyncMode.MODE4, ev.core, ev, ev.vec_sub_block_dim)
        else:
            # WAIT: pop from container; empty -> defer to kernel end (out-of-order)
            set_ev = self.get_intra_block_sync_info(ev.flag_id, ev.core)
            if set_ev is None:
                kernel.deferred_intra_waits.append(ev)
        return []

    def process_intra_block_event_per_core(self, kernel: "KernelScope", ev: "SyncEvent") -> List["Violation"]:
        """Per-core block_sync counter mode (mix_mode_force=False fallback).

        In non-cross-core scenarios, SET/WAIT_INTRA_BLOCK pairs by flag_id counts:
          SET: DUP_SET_UNCONSUMED if an unconsumed SET exists; otherwise increment the counter.
          WAIT: BLOCK_WAIT_PREMATURE if counter=0; otherwise decrement the counter.
        """
        blk = kernel.block_sync.setdefault(ev.flag_id, BlockSyncState())
        is_set = ev.op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI")
        if is_set:
            if blk.set_count - blk.wait_count > 0:
                return [
                    _sync_violation(
                        "DUP_SET_UNCONSUMED",
                        kernel.kernel_id,
                        ev,
                        f"block_id={ev.flag_id} unconsumed={blk.set_count - blk.wait_count}, "
                        f"set={blk.set_count} wait={blk.wait_count}",
                    )
                ]
            blk.set_count += 1
            blk.counter += 1
            blk.arrived_pcs.append(ev.pc)
        else:
            if blk.counter <= 0:
                return [
                    _sync_violation(
                        "BLOCK_WAIT_PREMATURE",
                        kernel.kernel_id,
                        ev,
                        f"block_id={ev.flag_id} counter=0, set_count={blk.set_count}, "
                        f"arrived_pcs={[hex(p) for p in blk.arrived_pcs]}",
                    )
                ]
            blk.wait_count += 1
            blk.counter -= 1
        return []

    def pending_sets(self) -> List[Tuple[int, int, str, "SyncEvent"]]:
        """Collect unconsumed SET events for kernel-end checks.

        Returns ``(group, idx, kind, ev)`` tuples, where ``kind`` is the core type that issued SET.
        """
        result = []
        for (group, idx), flag_map in self._block_sync.items():
            kind = _block_idx_to_core_kind(idx)
            for info in flag_map.values():
                for ev in info.setVec0:
                    result.append((group, idx, kind, ev))
                for ev in info.setVec1:
                    result.append((group, idx, kind, ev))
                for ev in info.setVec2:
                    result.append((group, idx, kind, ev))
                for ev in info.setVec4:
                    result.append((group, idx, kind, ev))
        return result

    def pending_waits(self) -> List[Tuple[int, int, str, "SyncEvent"]]:
        """Collect SET events pushed to WAIT queues but not consumed for kernel-end leak checks.

        ``waitVec`` normally holds signals pushed by SET while waiting for WAIT.
        """
        result = []
        for (group, idx), flag_map in self._block_sync.items():
            kind = _block_idx_to_core_kind(idx)
            for info in flag_map.values():
                for ev in info.waitVec:
                    result.append((group, idx, kind, ev))
                for ev in info.intrablockwaitVec:
                    result.append((group, idx, kind, ev))
        return result


@dataclass
class KernelScope:
    kernel_id: str = "unknown"
    event_pools: Dict[Tuple[str, str, int], EventPoolState] = field(default_factory=dict)
    cross_core: Dict[Tuple[str, int], CrossCoreState] = field(default_factory=dict)
    # buf stall detection state, keyed by (core, buf_id).
    buf_stall: Dict[Tuple[str, int], BufSlotState] = field(default_factory=dict)
    block_sync: Dict[int, BlockSyncState] = field(default_factory=dict)
    ffts_container: CrossCoreSyncContainer = field(default_factory=CrossCoreSyncContainer)
    # MODE4 waits that arrive out of order are deferred and retried at kernel end.
    deferred_intra_waits: List = field(default_factory=list)
    mix_mode_enabled: bool = False
    syncall_counts: Dict[str, int] = field(default_factory=dict)
    end_pc: int = 0
    end_id: int = 0
    end_core: str = ""
    end_file: str = ""


@dataclass
class Violation:
    rule: str
    kernel_id: str
    core: str
    pc: int
    instr_id: int
    op: str
    pipe: str
    trig_pipe: str
    flag_id: int
    detail: str
    file: str = ""
    line_no: int = 0
    raw: str = ""
    cycle: int = 0
    src_file: str = ""
    src_line: int = 0

    RULE_EXPLAIN = {
        "FLAG_ID_OOB": "flag_id is out of bounds (hardware limit is 16, current value >= 16)",
        "REDUNDANT_SET_FLAG": "two consecutive SET_FLAG operations on the same pipe are identical and redundant",
        "REDUNDANT_WAIT_FLAG": "two consecutive WAIT_FLAG operations on the same pipe are identical and redundant",
        "DUP_SET_UNCONSUMED": "a previous SET in the same event pool was not consumed by WAIT before another SET",
        "WAIT_NO_ARRIVE": "WAIT_FLAG cannot find a matching SET_FLAG for (pipe, trig_pipe, flag_id)",
        "WAIT_OVERCONSUME": "WAIT count exceeds SET count (WAIT occurs after all SETs are consumed)",
        "UNCONSUMED_ARRIVE": "at least one SET remains unconsumed by WAIT at kernel end (leak)",
        "UNCONSUMED_CROSS_CORE": "a cross-core SET remains unconsumed by the peer WAIT at kernel end",
        "WAIT_DEV_NO_SET": "WAIT_FLAG_DEV has no preceding SET_CROSS_CORE (counter is already zero)",
        "RLS_BUF_OVERCONSUME": "the number of RLS_BUF operations exceeds GET_BUF (buffer slots are over-released)",
        "BUF_STALL": (
            "GET_BUF waits for RLS_BUF but releases are insufficient or the implicit buffer is "
            "exhausted; the pipeline will stall"
        ),
        "BLOCK_WAIT_PREMATURE": "WAIT_INTRA_BLOCK sees block counter=0 (there is no preceding SET_INTRA_BLOCK)",
        "SYNCALL_COUNT_MISMATCH": (
            "SyncAll() call counts differ across cores (some cores call it fewer times, deadlock risk)"
        ),
        "MIX_MODE_ID_OOB_VEC": "the VECCORE sync id exceeds the upper limit of 15",
        "MIX_MODE_ID_OOB_CUBE": "the CUBECORE sync id exceeds the upper limit of 31",
        "MIX_MODE_WAIT_NO_ARRIVE": "WAIT_INTRA_BLOCK has no preceding SET_INTRA_BLOCK for the same sync_id",
        "MIX_MODE_UNCONSUMED": "an intra-block SET remains unconsumed by the peer WAIT at kernel end",
    }

    def format_text(self) -> str:
        """Render instruction fields as key=value with an explanation.

        KERNEL_END violations have no input instruction and print only the rule name and detail.
        """
        explain = self.RULE_EXPLAIN.get(self.rule, "")
        if not self.raw:
            return "\n".join(
                [
                    f"  [{self.rule}] (kernel-end check, no matching input instruction)",
                    f"    meaning: {explain}",
                    f"    detail: {self.detail}",
                    "",
                ]
            )
        kv = (
            f"cycle={self.cycle} "
            f"pc=0x{self.pc:x} "
            f"instr_id={self.instr_id} "
            f"op={self.op} "
            f"pipe={self.pipe} "
            f"trig_pipe={self.trig_pipe} "
            f"flag_id={self.flag_id} "
            f"core={self.core}"
        )
        out = [
            f"  [{self.rule}] {kv}",
            f"    meaning: {explain}",
            f"    detail: {self.detail}",
        ]
        if self.src_line:
            out.append(f"    src: {self.src_file}:{self.src_line}")
        out.append("")
        return "\n".join(out)

    def to_json(self) -> dict:
        """Return the serialized dict for this violation (field subset consistent with format_json)."""
        return {
            "rule": self.rule,
            "kernel_id": self.kernel_id,
            "core": self.core,
            "pc": self.pc,
            "instr_id": self.instr_id,
            "op": self.op,
            "pipe": self.pipe,
            "trig_pipe": self.trig_pipe,
            "flag_id": self.flag_id,
            "detail": self.detail,
            "src_file": self.src_file,
            "src_line": self.src_line,
        }


def _sync_violation(rule: str, kernel_id: str, ev: SyncEvent, detail: str) -> Violation:
    """Build a sync-instruction Violation from SyncEvent fields."""
    return Violation(
        rule=rule,
        kernel_id=kernel_id,
        core=ev.core,
        pc=ev.pc,
        instr_id=ev.instr_id,
        op=ev.op,
        pipe=ev.pipe,
        trig_pipe=ev.trig_pipe,
        flag_id=ev.flag_id,
        detail=detail,
        file=ev.file,
        line_no=ev.line_no,
        raw=ev.raw,
        cycle=ev.cycle,
    )


class SyncRule(ABC):
    """Sync-instruction rule base class using the Strategy pattern; each rule matches a set of ops and runs check."""

    ops: Tuple[str, ...] = ()

    @abstractmethod
    def check(self, kernel: "KernelScope", ev: SyncEvent) -> List[Violation]:
        raise NotImplementedError


class SetFlagRule(SyncRule):
    ops = ("SET_FLAG", "SET_FLAGI", "HSET_FLAG")

    def check(self, kernel, ev):
        k = (ev.pipe, ev.trig_pipe, ev.flag_id)
        pool = kernel.event_pools.setdefault(k, EventPoolState())
        hits = []
        if pool.set_count - pool.wait_count > 0:
            hits.append(
                _sync_violation(
                    "DUP_SET_UNCONSUMED",
                    kernel.kernel_id,
                    ev,
                    f"unconsumed={pool.set_count - pool.wait_count}, "
                    f"last_set_pc=0x{pool.last_set_pc:x} last_set_id={pool.last_set_id}",
                )
            )
        pool.set_count += 1
        pool.last_set_pc = ev.pc
        pool.last_set_id = ev.instr_id
        return hits


class WaitFlagRule(SyncRule):
    ops = ("WAIT_FLAG", "WAIT_FLAGI", "HWAIT_FLAG")

    def check(self, kernel, ev):
        k = (ev.pipe, ev.trig_pipe, ev.flag_id)
        pool = kernel.event_pools.setdefault(k, EventPoolState())
        hits = []
        if pool.set_count == 0:
            hits.append(
                _sync_violation(
                    "WAIT_NO_ARRIVE",
                    kernel.kernel_id,
                    ev,
                    f"no SET_FLAG with (pipe={ev.pipe}, trig={ev.trig_pipe}, flag_id={ev.flag_id})",
                )
            )
        elif pool.wait_count >= pool.set_count:
            hits.append(
                _sync_violation(
                    "WAIT_OVERCONSUME",
                    kernel.kernel_id,
                    ev,
                    f"wait_count={pool.wait_count} >= set_count={pool.set_count}",
                )
            )
        pool.wait_count += 1
        return hits


class SetCrossCoreRule(SyncRule):
    ops = ("SET_CROSS_CORE",)

    def check(self, kernel, ev):
        """Pair SET_CROSS_CORE as a MODE0 network-wide broadcast."""
        kernel.ffts_container.set_block_sync_info(ev.flag_id, FftsSyncMode.MODE0, ev.core, ev, ev.vec_sub_block_dim)
        # Keep the cross_core counter for UNCONSUMED_CROSS_CORE compatibility.
        cc = kernel.cross_core.setdefault((ev.core, ev.flag_id), CrossCoreState())
        cc.set_count += 1
        cc.counter += 1
        return []


class WaitFlagDevRule(SyncRule):
    ops = ("WAIT_FLAG_DEV", "WAIT_FLAG_DEVI")

    def check(self, kernel, ev):
        """WAIT_FLAG_DEV consumes one MODE0 signal from waitVec."""
        cc = kernel.cross_core.setdefault((ev.core, ev.flag_id), CrossCoreState())
        hits = []
        set_ev = kernel.ffts_container.get_block_sync_info(ev.flag_id, ev.core)
        if set_ev is None and cc.counter <= 0:
            hits.append(
                _sync_violation(
                    "WAIT_DEV_NO_SET", kernel.kernel_id, ev, "counter=0 before wait (no SET_CROSS_CORE arrived)"
                )
            )
        cc.wait_count += 1
        cc.counter -= 1
        return hits


class GetBufRule(SyncRule):
    ops = ("GET_BUF",)

    def check(self, kernel, ev):
        hits = []
        # BUF_ID_OOB precheck
        if ev.flag_id >= MAX_BUF_ID:
            hits.append(_sync_violation("BUF_ID_OOB", kernel.kernel_id, ev, f"buf_id={ev.flag_id} >= {MAX_BUF_ID}"))

        # ---- stall detection ----
        # key = (core, buf_id), available = rls_queue.size() + 1 (one implicit initial buffer)
        # consumed = number of consumed GET_BUF operations
        stall_key = (ev.core, ev.flag_id)
        stall_bs = kernel.buf_stall.setdefault(stall_key, BufSlotState())
        available = len(stall_bs.rls_queue) + 1  # one implicit initial buffer
        consumed = stall_bs.consumed

        if ev.rls_count == 0:
            # first GET_BUF without a preceding RLS_BUF: check consumed + 1 > available
            if consumed + 1 > available:
                hits.append(
                    _sync_violation(
                        "BUF_STALL",
                        kernel.kernel_id,
                        ev,
                        f"buf_id={ev.flag_id} first GET_BUF but the implicit buffer is exhausted "
                        f"(consumed={consumed} >= available={available}), "
                        f"consecutive GET_BUF without interleaved RLS_BUF will stall",
                    )
                )
        else:
            # rlsCount > 0: require rls_queue.size() >= rls_count and consumed + 1 <= available
            if len(stall_bs.rls_queue) < ev.rls_count or consumed + 1 > available:
                hits.append(
                    _sync_violation(
                        "BUF_STALL",
                        kernel.kernel_id,
                        ev,
                        f"buf_id={ev.flag_id} GET_BUF waits for sufficient RLS_BUF "
                        f"(rls_queue={len(stall_bs.rls_queue)} < rls_count={ev.rls_count}, "
                        f"consumed={consumed}, available={available}); pipeline will stall",
                    )
                )
        stall_bs.consumed += 1

        return hits


class RlsBufRule(SyncRule):
    ops = ("RLS_BUF",)

    def check(self, kernel, ev):
        hits = []
        # RLS_BUF events enter the FIFO queue.
        stall_key = (ev.core, ev.flag_id)
        stall_bs = kernel.buf_stall.setdefault(stall_key, BufSlotState())
        stall_bs.rls_queue.append(ev)

        return hits


class SyncAllRule(SyncRule):
    ops = ("SYNC_ALL",)

    def check(self, kernel, ev):
        kernel.syncall_counts[ev.core] = kernel.syncall_counts.get(ev.core, 0) + 1
        return []


class SyncRuleRegistry:
    """op -> SyncRule dispatch table, removing the if/elif chain in detect()."""

    def __init__(self):
        rules = [
            SetFlagRule(),
            WaitFlagRule(),
            SetCrossCoreRule(),
            WaitFlagDevRule(),
            GetBufRule(),
            RlsBufRule(),
            SyncAllRule(),
        ]
        self._op_map: Dict[str, SyncRule] = {}
        for rule in rules:
            for op in rule.ops:
                self._op_map[op] = rule

    def get(self, op: str) -> Optional[SyncRule]:
        return self._op_map.get(op)


def _detect_end_hits(kernel: KernelScope) -> List[Violation]:
    """Kernel-end checks: unconsumed SET / cross-core SET / block SET / FFTS pending / SyncAll."""
    hits = []
    hits.extend(_detect_unconsumed_pools(kernel))
    hits.extend(_detect_unconsumed_cross_core(kernel))
    hits.extend(_detect_unconsumed_blocks(kernel))
    hits.extend(_detect_buf_overconsume(kernel))
    # Unconsumed SET events in the FFTS cross-core container (residual setVec queues for MODE0~4)
    if kernel.mix_mode_enabled:
        _detect_ffts_pending(kernel, hits)
    if len(kernel.syncall_counts) > 1:
        counts = list(kernel.syncall_counts.values())
        max_count = max(counts)
        if max(counts) != min(counts):
            for core, count in kernel.syncall_counts.items():
                if count < max_count:
                    hits.append(
                        _kernel_end_violation(
                            kernel,
                            "SYNCALL_COUNT_MISMATCH",
                            flag_id=0,
                            detail=(
                                f"core={core} SyncAll count={count}, max={max_count} "
                                "(other cores call it more times, deadlock risk)"
                            ),
                        )
                    )
    return hits


def _kernel_end_violation(
    kernel: KernelScope,
    rule: str,
    *,
    flag_id: int,
    detail: str,
    pipe: str = "?",
    trig_pipe: str = "?",
) -> Violation:
    """Build a kernel-end violation without a matching input instruction."""
    return Violation(
        rule=rule,
        kernel_id=kernel.kernel_id,
        core=kernel.end_core,
        pc=kernel.end_pc,
        instr_id=kernel.end_id,
        op="KERNEL_END",
        pipe=pipe,
        trig_pipe=trig_pipe,
        flag_id=flag_id,
        detail=detail,
        file=kernel.end_file,
        line_no=0,
    )


def _detect_unconsumed_pools(kernel: KernelScope) -> List[Violation]:
    """Report SET counts that remain greater than WAIT counts in event pools."""
    hits = []
    for key, pool in kernel.event_pools.items():
        if pool.set_count <= pool.wait_count:
            continue
        hits.append(
            _kernel_end_violation(
                kernel,
                "UNCONSUMED_ARRIVE",
                flag_id=key[2],
                pipe=key[0],
                trig_pipe=key[1],
                detail=f"unconsumed={pool.set_count - pool.wait_count}, set={pool.set_count} wait={pool.wait_count}",
            )
        )
    return hits


def _detect_unconsumed_cross_core(kernel: KernelScope) -> List[Violation]:
    """Report MODE0 cross-core SET counters that were not consumed by WAIT."""
    hits = []
    for key, cross_core in kernel.cross_core.items():
        if cross_core.counter <= 0:
            continue
        hits.append(
            _kernel_end_violation(
                kernel,
                "UNCONSUMED_CROSS_CORE",
                flag_id=key[1],
                detail=f"key={key} counter={cross_core.counter}",
            )
        )
    return hits


def _detect_unconsumed_blocks(kernel: KernelScope) -> List[Violation]:
    """Report intra-block SET counters that were not consumed by WAIT."""
    hits = []
    for block_id, block in kernel.block_sync.items():
        if block.set_count <= block.wait_count:
            continue
        hits.append(
            _kernel_end_violation(
                kernel,
                "UNCONSUMED_ARRIVE",
                flag_id=block_id,
                detail=(
                    f"block_id={block_id} set={block.set_count} wait={block.wait_count}, "
                    f"unconsumed={block.set_count - block.wait_count}"
                ),
            )
        )
    return hits


def _detect_buf_overconsume(kernel: KernelScope) -> List[Violation]:
    """Report GET_BUF counts above RLS_BUF count plus the implicit buffer."""
    hits = []
    for (core, buf_id), state in kernel.buf_stall.items():
        available = len(state.rls_queue) + 1
        if state.consumed <= available:
            continue
        hits.append(
            _kernel_end_violation(
                kernel,
                "RLS_BUF_OVERCONSUME",
                flag_id=buf_id,
                detail=(
                    f"key=({core}, {buf_id}) consumed={state.consumed} > "
                    f"available={available} (rls={len(state.rls_queue)} + 1 implicit), "
                    "GET_BUF count exceeds RLS_BUF plus the implicit buffer"
                ),
            )
        )
    return hits


def _pending_set_violation(kernel: KernelScope, kind: str, ev: SyncEvent) -> Violation:
    """Build a violation for a SET that was not consumed by its peer WAIT."""
    if ev.op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI"):
        rule = "MIX_MODE_UNCONSUMED"
        detail = f"sync_id={ev.flag_id} src={kind} SET not consumed by peer WAIT"
    else:
        rule = "UNCONSUMED_CROSS_CORE"
        detail = f"flag_id={ev.flag_id} src={kind} SET_CROSS_CORE not consumed by peer WAIT"
    return _event_violation(kernel, rule, kind, ev, detail)


def _pending_wait_violation(kernel: KernelScope, kind: str, ev: SyncEvent) -> Violation:
    """Build a violation for a SET routed to a WAIT queue that was never consumed."""
    if ev.op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI"):
        rule = "MIX_MODE_UNCONSUMED"
        detail = f"sync_id={ev.flag_id} SET pushed to {kind} but peer did not WAIT consume it"
    else:
        rule = "UNCONSUMED_CROSS_CORE"
        detail = f"flag_id={ev.flag_id} SET_CROSS_CORE pushed to {kind} but peer did not WAIT consume it"
    return _event_violation(kernel, rule, kind, ev, detail)


def _event_violation(kernel: KernelScope, rule: str, kind: str, ev: SyncEvent, detail: str) -> Violation:
    """Build a pending-event violation from SyncEvent fields."""
    return Violation(
        rule=rule,
        kernel_id=kernel.kernel_id,
        core=ev.core,
        pc=ev.pc,
        instr_id=ev.instr_id,
        op=ev.op,
        pipe=ev.pipe,
        trig_pipe=ev.trig_pipe,
        flag_id=ev.flag_id,
        detail=detail,
        file=ev.file,
        line_no=ev.line_no,
        raw=ev.raw,
        cycle=ev.cycle,
    )


def _detect_ffts_pending(kernel: KernelScope, hits: List[Violation]) -> None:
    """Check incompletely paired sync events in the FFTS container."""
    for w_ev in kernel.deferred_intra_waits:
        set_ev = kernel.ffts_container.get_intra_block_sync_info(w_ev.flag_id, w_ev.core)
        if set_ev is None:
            kind = core_kind(w_ev.core)
            hits.append(
                _sync_violation(
                    "MIX_MODE_WAIT_NO_ARRIVE",
                    kernel.kernel_id,
                    w_ev,
                    f"sync_id={w_ev.flag_id} no preceding SET "
                    f"({'VEC0 and VEC1' if kind == 'CUBE' else 'CUBE'} did not SET)",
                )
            )
    kernel.deferred_intra_waits.clear()

    for _group, _idx, kind, ev in kernel.ffts_container.pending_sets():
        hits.append(_pending_set_violation(kernel, kind, ev))
    for _group, _idx, kind, ev in kernel.ffts_container.pending_waits():
        hits.append(_pending_wait_violation(kernel, kind, ev))


def detect(kernel: KernelScope, events: List[SyncEvent], mix_mode_force: bool = True) -> List[Violation]:
    registry = SyncRuleRegistry()
    hits = []
    for ev in events:
        is_intra = ev.op in ("SET_INTRA_BLOCK", "SET_INTRA_BLOCKI", "WAIT_INTRA_BLOCK", "WAIT_INTRA_BLOCKI")

        # INTRA_BLOCK is handled by the cross-core sync container and supports per-core counter fallback.
        if is_intra:
            if mix_mode_force:
                hits.extend(kernel.ffts_container.process_intra_block_event(kernel, ev))
            else:
                hits.extend(kernel.ffts_container.process_intra_block_event_per_core(kernel, ev))
            continue

        # FLAG_ID_OOB precheck; INTRA_BLOCK limits depend on core kind and are handled in 6a/6b
        if (
            ev.flag_id >= 16
            and not is_intra
            and ev.op in ("SET_FLAG", "SET_FLAGI", "WAIT_FLAG", "WAIT_FLAGI", "SET_CROSS_CORE", "WAIT_FLAG_DEV")
        ):
            hits.append(_sync_violation("FLAG_ID_OOB", kernel.kernel_id, ev, f"flag_id={ev.flag_id} >= 16"))

        rule = registry.get(ev.op)
        if rule:
            hits.extend(rule.check(kernel, ev))

    hits.extend(_detect_end_hits(kernel))
    return hits


# ============================================================
# Part 2B: redundant sync detection (DoRedundancyCheck)
# ============================================================
#   - SET_FLAG/HSET_FLAG redundancy pipe key = srcPipe (PIPE field)
#   - WAIT_FLAG/HWAIT_FLAG redundancy pipe key = dstPipe (TRIGGER PIPE field)
#   - comparison fields = (opType, srcPipe, dstPipe, eventId); all must match to report redundancy
#   - non-sync instructions clear the previous instruction saved for that pipe; ignore PIPE_BARRIER
#   - on redundancy, report both the previous and current instructions


def _redundancy_key(ev: InstrEvent) -> str:
    """Redundancy pipe key: SET uses pipe, WAIT uses trig_pipe, and other sync instructions use pipe."""
    if ev.op in ("SET_FLAG", "SET_FLAGI", "HSET_FLAG"):
        return ev.pipe or ev.header_pipe
    if ev.op in ("WAIT_FLAG", "WAIT_FLAGI", "HWAIT_FLAG"):
        return ev.trig_pipe or ev.header_pipe
    return ev.pipe or ev.header_pipe


def _same_sync_params(prev: InstrEvent, ev: InstrEvent) -> bool:
    """Check whether two sync instructions have exactly the same parameters (redundancy)."""
    return prev.op == ev.op and prev.pipe == ev.pipe and prev.trig_pipe == ev.trig_pipe and prev.flag_id == ev.flag_id


def _redundancy_hits(kernel: KernelScope, prev: InstrEvent, ev: InstrEvent) -> List[Violation]:
    """Build redundancy warnings by reporting both the previous and current instructions."""
    rule = "REDUNDANT_SET_FLAG" if ev.op in ("SET_FLAG", "SET_FLAGI", "HSET_FLAG") else "REDUNDANT_WAIT_FLAG"
    return [
        Violation(
            rule=rule,
            kernel_id=kernel.kernel_id,
            core=prev.core,
            pc=prev.pc,
            instr_id=prev.instr_id,
            op=prev.op,
            pipe=prev.pipe,
            trig_pipe=prev.trig_pipe,
            flag_id=prev.flag_id,
            detail=f"identical to next {ev.op} (pc=0x{ev.pc:x} instr_id={ev.instr_id}); "
            f"same parameters issued consecutively on the same pipe with no intervening operation",
            file=prev.file,
            line_no=prev.line_no,
            raw=prev.raw,
            cycle=prev.cycle,
        ),
        Violation(
            rule=rule,
            kernel_id=kernel.kernel_id,
            core=ev.core,
            pc=ev.pc,
            instr_id=ev.instr_id,
            op=ev.op,
            pipe=ev.pipe,
            trig_pipe=ev.trig_pipe,
            flag_id=ev.flag_id,
            detail=f"identical to previous {prev.op} (pc=0x{prev.pc:x} instr_id={prev.instr_id}); "
            f"same parameters issued consecutively on the same pipe with no intervening operation",
            file=ev.file,
            line_no=ev.line_no,
            raw=ev.raw,
            cycle=ev.cycle,
        ),
    ]


def detect_redundancy(kernel: KernelScope, stream: List[InstrEvent]) -> List[Violation]:
    """Warn when identical consecutive SET/WAIT operations reuse a pipe.

    BAR does not participate in redundancy checks and does not clear the record.
    """
    hits: List[Violation] = []
    last_sync: Dict[str, InstrEvent] = {}
    for ev in stream:
        if not ev.is_sync:
            # non-sync instructions clear the previous sync instruction saved for this pipe
            if ev.header_pipe:
                last_sync.pop(ev.header_pipe, None)
            continue
        if ev.op == "BAR":
            continue
        key = _redundancy_key(ev)
        prev = last_sync.get(key)
        if prev is not None and _same_sync_params(prev, ev):
            hits.extend(_redundancy_hits(kernel, prev, ev))
        last_sync[key] = ev
    return hits


# ============================================================
# Part 3: main flow - kernel scope splitting and output
# ============================================================


def split_kernels(events: List[SyncEvent]) -> List[Tuple[str, List[SyncEvent]]]:
    """Split KernelScope by core when KERNEL_END is missing to avoid cross-core state pollution."""
    if not events:
        return []
    by_core = {}
    for ev in events:
        by_core.setdefault(ev.core, []).append(ev)
    return [(core, evs) for core, evs in by_core.items()]


class SyncInstructionDetector:
    """Run the collect -> detect -> format pipeline."""

    def __init__(self, mix_mode_force: bool = True):
        self.mix_mode_force = mix_mode_force

    def run(self, input_path: str) -> List[Violation]:
        records = collect_record_instructions(input_path)
        events = [_record_instruction_to_sync_event(record) for record in records if record.is_sync]
        events.sort(key=lambda event: (event.cycle, event.instr_id))
        precompute_rls_count(events)  # precompute RLS_BUF counts and fill GET_BUF.rls_count
        stream = [_record_instruction_to_instr_event(record) for record in records]
        stream.sort(key=lambda event: (event.cycle, event.instr_id))
        stream_by_core: Dict[str, List[InstrEvent]] = {}
        for ev in stream:
            stream_by_core.setdefault(ev.core, []).append(ev)
        all_hits: List[Violation] = []

        if self.mix_mode_force:
            kernel = KernelScope(kernel_id="mix_mode")
            dispatch_events = events
            if dispatch_events:
                last = dispatch_events[-1]
                kernel.end_pc = last.pc
                kernel.end_id = last.instr_id
                kernel.end_core = last.core
                kernel.end_file = last.file
            all_hits.extend(detect(kernel, dispatch_events, mix_mode_force=True))
            for core, evs in stream_by_core.items():
                all_hits.extend(detect_redundancy(KernelScope(kernel_id=core), evs))
            return all_hits

        for kid, evs in split_kernels(events):
            kernel = KernelScope(kernel_id=kid)
            if evs:
                last = evs[-1]
                kernel.end_pc = last.pc
                kernel.end_id = last.instr_id
                kernel.end_core = last.core
                kernel.end_file = last.file
            all_hits.extend(detect(kernel, evs, mix_mode_force=self.mix_mode_force))
            all_hits.extend(detect_redundancy(kernel, stream_by_core.get(kid, [])))
        return all_hits


def run(input_path: str, mix_mode_force: bool = True) -> List[Violation]:
    return SyncInstructionDetector(mix_mode_force=mix_mode_force).run(input_path)


def annotate_with_source(hits: List[Violation], mapper) -> List[Violation]:
    """Use PcSourceMapper to annotate source lines for each violation in place.

    Returns unchanged when the mapper is unavailable. Queries source mapping in batches by kernel_id.
    """
    if not mapper or not getattr(mapper, "available", False):
        return hits
    by_kernel: Dict[str, List[int]] = {}
    for v in hits:
        if v.pc:
            by_kernel.setdefault(v.kernel_id, []).append(v.pc)
    lookup = {}
    for kid, pcs in by_kernel.items():
        lookup.update(mapper.lookup_many(kid, pcs))
    for v in hits:
        loc = lookup.get(v.pc) if v.pc else None
        if loc:
            v.src_file = loc.src_file
            v.src_line = loc.line
    return hits


def format_text(hits: List[Violation]) -> str:
    """Print instruction fields as key=value with an English explanation.

    KERNEL_END violations have no input instruction and print only the rule name and detail.
    """
    if not hits:
        return "[sync_instruction_detection] No violations found.\n"
    lines = [f"[sync_instruction_detection] Found {len(hits)} violation(s) — offending input lines:\n"]
    by_kernel = {}
    for v in hits:
        by_kernel.setdefault(v.kernel_id, []).append(v)
    for kid, vs in by_kernel.items():
        lines.append(f"=== Kernel: {kid} ({len(vs)} violations) ===")
        for v in vs:
            lines.append(v.format_text())
        lines.append("")
    return "\n".join(lines)


def format_json(hits: List[Violation]) -> str:
    return json.dumps([v.to_json() for v in hits], indent=2)
