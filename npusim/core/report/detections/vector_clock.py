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

"""Shared vector clock module.

Used by all three detections (per-pipe / per-block / per-subcore).

Core operations:
  - update_logic_time(vt, idx)     — vt[idx]++ (C++ UpdateLogicTime)
  - update_vector_time(src, dst)   — dst[i] = max(src[i], dst[i]) (C++ UpdateVectorTime)
  - is_concurrent_partial(t1, t2, p1, p2) — partial-order concurrency (C++ IsNotHappensBefore)
  - vector_clock_le / has_happens_before_relation — full-vector partial order / happens-before
  - signal_store / signal_acquire / barrier_sync — cross-core / intra-core sync primitives

Partial vs full concurrency check:
  partial: only the p1/p2 dimensions. Fits per-pipe VC: cross-pipe sync only flows
    along pipe pairs at SET_FLAG/WAIT_FLAG, so other dimensions cannot affect p1<->p2
    happens-before.
  full: scans all dimensions. Fits per-block / per-subcore VC: the BAR global barrier
    and multi-producer SET_CROSS_CORE may establish happens-before via a third party,
    which the partial check would miss.
"""

from typing import List, Tuple, Dict, Optional


VectorTime = List[int]


def update_logic_time(vt: VectorTime, idx: int) -> None:
    """vt[idx]++ — C++ UpdateLogicTime(t, pipe)."""
    if 0 <= idx < len(vt):
        vt[idx] += 1


def update_vector_time(src: VectorTime, dst: VectorTime) -> None:
    """dst[i] = max(src[i], dst[i]) — C++ UpdateVectorTime(in, out).

    Merges src's vector time into dst (acquire semantics)."""
    n = min(len(src), len(dst))
    for i in range(n):
        if src[i] > dst[i]:
            dst[i] = src[i]


def is_concurrent_partial(t1: Tuple[int, ...], t2: Tuple[int, ...], p1: int, p2: int) -> bool:
    """Partial-order concurrency — C++ IsNotHappensBefore(t1, t2, t1Pipe, t2Pipe).

    Two events on their own pipes are concurrent when one pipe's time is greater
    and the other's is smaller. Returns False when t1 is entirely before or
    after t2 (happens-before).

    Fits per-pipe VC: cross-pipe sync only flows along pipe pairs at
    SET_FLAG/WAIT_FLAG, so other dimensions cannot affect p1<->p2 happens-before."""
    if p1 == p2:
        return False
    if p1 >= len(t1) or p2 >= len(t1) or p1 >= len(t2) or p2 >= len(t2):
        return False
    cond_a = t1[p1] < t2[p1] and t1[p2] > t2[p2]
    cond_b = t1[p2] < t2[p2] and t1[p1] > t2[p1]
    return cond_a or cond_b


def vector_clock_le(v1: VectorTime, v2: VectorTime) -> bool:
    """Full-vector partial order: v1 <= v2 iff v1[i] <= v2[i] for all i."""
    return all(a <= b for a, b in zip(v1, v2))


def has_happens_before_relation(v1: Optional[VectorTime], v2: Optional[VectorTime]) -> bool:
    """Whether two events have a happens-before relation (either direction).

    Cross-core race detection: happens-before (either direction) → synced, not
    reported; concurrent → reported."""
    if v1 is None or v2 is None:
        return False
    return vector_clock_le(v1, v2) or vector_clock_le(v2, v1)


# ---- Signal/Wait sync primitives (shared by cross-core / intra-core VC) ----
# SET_CROSS_CORE / SET_INTRA_BLOCK: signal[flag_id][participant] = vc snapshot
# WAIT_FLAG_DEV / WAIT_INTRA_BLOCK: vc = max(vc, max_over_signal), then advance


def signal_store(signal: Dict[int, Dict[int, VectorTime]], flag_id: int, participant: int, vc: VectorTime) -> None:
    """SET: store the participant's current VC snapshot in signal[flag_id][participant]."""
    signal.setdefault(flag_id, {})[participant] = list(vc)


def signal_acquire(signal: Dict[int, Dict[int, VectorTime]], flag_id: int, vc: VectorTime) -> bool:
    """WAIT: vc = max(vc, max_over_signal[flag_id]), returns True.

    Merges all participant VC snapshots in signal[flag_id] with max (acquire).
    Missing signal (no SET snapshot) returns False (silently skipped)."""
    if flag_id not in signal:
        return False
    for sv in signal[flag_id].values():
        update_vector_time(sv, vc)
    return True


def barrier_sync(vc: Dict[int, VectorTime], idx: Dict[int, int], participants: List[int]) -> None:
    """BAR global sync: all participant clocks take the component-wise max, then advance.

    SyncAll semantics: merge all block/subcore VCs with max, each then increments."""
    n = len(participants)
    if n == 0:
        return
    merged = [0] * n
    for b in participants:
        for i in range(n):
            if b in vc and i < len(vc[b]) and vc[b][i] > merged[i]:
                merged[i] = vc[b][i]
    for b in participants:
        vc[b] = list(merged)
        update_logic_time(vc[b], idx.get(b, 0))
