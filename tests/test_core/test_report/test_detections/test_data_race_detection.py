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

"""Unit tests for npusim.core.report.detections.data_race_detection.

The check reads the helper record DB (ExecutedInstructions / MemoryRWAccesses)
and validates WAW/RAW/WAR races (UB layer, GM layer and cross-pipe).
"""

import json
import random
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.report.detections.data_race_detection import (
    CrossCoreSyncEvent,
    GmEvent,
    GmViolation,
    IntraBlockSyncEvent,
    Interval,
    PipeIssEvent,
    RwEvent,
    SyncFlagPair,
    Violation,
    RaceCandidate,
    DataRaceDetector,
    RaceContext,
    _is_not_happens_before,
    build_pipe_map,
    build_ub_cross_core_vc,
    collect_cross_core_sync_events,
    collect_gm_rw_events,
    collect_issque_events,
    collect_buffer_rw_events,
    collect_buf_sync_pipes,
    collect_intra_block_sync_events,
    detect_cross_pipe_races,
    detect_gm_cross_core_races,
    detect_gm_pipeline_races,
    detect_same_pipe_races,
    detect_scan_races,
    detect_ub_cross_core_races,
    format_json,
    format_text,
    infer_pipe_from_instr_name,
    match_sync_flags,
    run,
)


def _write_record_db(root, rows, filename="record.db"):
    """Create a single-file helper record DB holding ExecutedInstructions rows.

    Row shape: (instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, params).
    Includes the contract-v7 MemoryRWAccesses table (with Size) so the
    DB-only buffer/issque readers are active.
    """
    con = sqlite3.connect(Path(root) / filename)
    con.executescript("""
        CREATE TABLE ExecutedInstructions(
            ExecInstrId INTEGER PRIMARY KEY,
            ExecInstrCoreId INTEGER,
            CoreTypeId INTEGER,
            ExecInstrName TEXT,
            ExecInstrTickStart INTEGER,
            InstrTypeId INTEGER,
            SourceInstrAddr INTEGER,
            ExtendParams TEXT);
        CREATE TABLE MemoryRWAccesses(
            AccessedAddress INTEGER NOT NULL,
            AccessMode VARCHAR(1) NOT NULL,
            AccessTime INTEGER NOT NULL,
            MemoryType INTEGER NOT NULL,
            ExecInstrId INTEGER,
            CoreId INTEGER NOT NULL,
            Size INTEGER,
            Step INTEGER);
    """)
    con.executemany(
        "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, json.dumps(params))
            for instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, params in rows
        ],
    )
    con.commit()
    con.close()


def _write_instr_db(root, rows):
    """Add ExecutedInstructions rows to the record DB in root (which must already exist).

    Row shape: (instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, params).
    """
    con = sqlite3.connect(Path(root) / "record.db")
    con.executemany(
        "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, json.dumps(params))
            for instr_id, core_id, core_type_id, name, tick, instr_type_id, pc, params in rows
        ],
    )
    con.commit()
    con.close()


def _write_mem_access_db(root, rows, filename="record.db"):
    """Create a helper record DB with a MemoryRWAccesses table.

    Row shape: (addr, mode, tick, memory_type, instr_id, core_id, size, step).
    ExecutedInstructions is created empty so the instr-join readers work.
    """
    con = sqlite3.connect(Path(root) / filename)
    con.executescript("""
        CREATE TABLE ExecutedInstructions(
            ExecInstrId INTEGER PRIMARY KEY,
            ExecInstrCoreId INTEGER,
            CoreTypeId INTEGER,
            ExecInstrName TEXT,
            ExecInstrTickStart INTEGER,
            InstrTypeId INTEGER,
            SourceInstrAddr INTEGER,
            ExtendParams TEXT);
        CREATE TABLE MemoryRWAccesses(
            AccessedAddress INTEGER NOT NULL,
            AccessMode VARCHAR(1) NOT NULL,
            AccessTime INTEGER NOT NULL,
            MemoryType INTEGER NOT NULL,
            ExecInstrId INTEGER,
            CoreId INTEGER NOT NULL,
            Size INTEGER,
            Step INTEGER);
    """)
    con.executemany(
        "INSERT INTO MemoryRWAccesses "
        "(AccessedAddress, AccessMode, AccessTime, MemoryType, ExecInstrId, CoreId, Size, Step) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()


def _scan_kind(rws, pipe_map, pairs, pushes, kind, queue_sync_pipes=None):
    """Filter one detect_scan_races() sweep by kind (replaces the removed entries)."""
    return [
        c for c in detect_scan_races(rws, pipe_map, pairs, pushes, queue_sync_pipes=queue_sync_pipes) if c.kind == kind
    ]


def _brute_force_gm_cross_core(evs):
    """O(n^2) reference: the pre-optimization pairwise scan of
    detect_gm_cross_core_races(), kept to regression-check the sweep version
    (same result set and order)."""
    hits = []
    evs = sorted(evs, key=lambda e: (e.tick, e.core, e.line_no))
    for i, a in enumerate(evs):
        a_iv = Interval(a.addr, a.addr + a.size)
        reported = set()
        for b in evs[i + 1 :]:
            if b.core == a.core:
                continue
            if b.instr_id == a.instr_id and b.tick == a.tick:
                continue
            b_iv = Interval(b.addr, b.addr + b.size)
            if not a_iv.overlap(b_iv):
                continue
            if a.kind == "WRITE" and b.kind == "WRITE":
                rule = "GM_CROSS_CORE_WAW"
            elif a.kind == "WRITE" and b.kind == "READ":
                rule = "GM_CROSS_CORE_RAW"
            elif a.kind == "READ" and b.kind == "WRITE":
                rule = "GM_CROSS_CORE_WAR"
            else:
                continue
            if rule in reported:
                continue
            reported.add(rule)
            inter_lo = max(a_iv.lo, b_iv.lo)
            inter_hi = min(a_iv.hi, b_iv.hi)
            cores = f"{a.core},{b.core}" if a.core < b.core else f"{b.core},{a.core}"
            hits.append(
                (rule, a.core, cores, inter_lo, inter_hi - inter_lo, a.instr_id, f"{a.instr_id},{b.instr_id}", a.tick)
            )
    return hits


def _canonical_cross_core(hits):
    return [(h.rule, h.core, h.cores, h.addr, h.size, h.instr_id, h.instr_ids, h.tick) for h in hits]


class WawCheckParseTests(unittest.TestCase):
    def test_collect_buffer_rw_events_reads_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(root, [(0x1000, "W", 150, 1, 2, 0, 32, 0)])
            rws = collect_buffer_rw_events(str(root))
            self.assertEqual(len(rws), 1)
            self.assertEqual(rws[0].kind, "WRITE")
            self.assertEqual(rws[0].addr, 0x1000)
            self.assertEqual(rws[0].size, 32)
            self.assertEqual(rws[0].buf_kind, "ub")

    def test_collect_buffer_rw_events_uses_instruction_owner_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # contract-v7: MemoryRWAccesses.CoreId is always 0 and ExecInstrId
            # only holds the low 32 bits; the owner core lives in ExecutedInstructions.
            _write_mem_access_db(root, [(0x1000, "W", 150, 1, 9, 0, 32, 0)])
            _write_instr_db(
                root,
                [
                    (
                        9 | (1 << 32),
                        1,
                        3,
                        "MOV_SRC_TO_DST_ALIGNv2",
                        150,
                        0,
                        0x1234,
                        {"queue_type": "MTE2"},
                    )
                ],
            )
            rws = collect_buffer_rw_events(str(root))
            self.assertEqual(len(rws), 1)
            self.assertEqual(rws[0].core, 1)
            self.assertEqual(rws[0].sub, 1)
            self.assertEqual(rws[0].core_type_id, 3)
            self.assertEqual(rws[0].instr_name, "MOV_SRC_TO_DST_ALIGNv2")
            self.assertEqual(rws[0].pc, 0x1234)

    def test_collect_buffer_rw_events_uses_step_for_strided_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A strided write (Step=4) of 4 words: rows at addr 0x1000,0x1004,0x1008,0x100c,
            # each Size=4. The interval must reach the last word, not just Size bytes.
            _write_mem_access_db(
                root,
                [(0x1000 + 4 * i, "W", 150, 1, 2, 0, 4, 4) for i in range(4)],
            )
            rws = collect_buffer_rw_events(str(root))
            self.assertEqual(len(rws), 4)
            # Size=4, Step=4 -> each row covers (4-1)*4 = 12 bytes: [addr, addr+12]
            self.assertEqual(rws[0].size, 12)
            self.assertEqual(rws[0].addr, 0x1000)
            self.assertEqual(rws[-1].addr, 0x100C)

    def test_collect_buffer_rw_events_without_v7_db_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A DB without the MemoryRWAccesses.Size column is not v7
            con = sqlite3.connect(Path(root) / "record.db")
            con.executescript(
                "CREATE TABLE MemoryRWAccesses("
                "AccessedAddress INTEGER NOT NULL, AccessMode VARCHAR(1) NOT NULL, "
                "AccessTime INTEGER NOT NULL, MemoryType INTEGER NOT NULL, "
                "ExecInstrId INTEGER, CoreId INTEGER NOT NULL, Step INTEGER);"
            )
            con.commit()
            con.close()
            self.assertEqual(collect_buffer_rw_events(str(root)), [])
            self.assertEqual(collect_issque_events(str(root)), ([], [], []))

    def test_collect_buffer_rw_events_filters_cache_refill_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # instr_id=2: the store and its RMW refill read share (tick, pc, instr_id, addr, size)
            _write_mem_access_db(
                root,
                [
                    (0x1000, "W", 150, 1, 2, 0, 32, 0),
                    (0x1000, "R", 150, 1, 2, 0, 32, 0),
                    (0x1000, "R", 200, 1, 9, 0, 32, 0),
                ],
            )
            rws = collect_buffer_rw_events(str(root))
            # A READ with the same (cycle, pc, instr_id, addr, size) is the store's RMW
            # refill and gets filtered out
            self.assertEqual(len(rws), 2)
            self.assertEqual([r.kind for r in rws], ["WRITE", "READ"])
            self.assertEqual(rws[1].instr_id, 9)

    def test_collect_issque_events_and_pipe_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    # push STORE_2DV2 on the vec pipe (SIMD queue)
                    (129, 0, 2, "STORE_2DV2", 100, 0, 0x204, {"queue_type": "SIMD"}),
                    # SET_FLAG releasing the vec (source) pipe
                    (
                        131,
                        0,
                        2,
                        "SET_FLAG",
                        110,
                        0,
                        0x20C,
                        {"flag_id": 0, "target_pipe": "VEC", "consumer_pipe": "MTE2", "queue_type": "SCALAR"},
                    ),
                    # WAIT_FLAG releasing the mte2 (destination) pipe
                    (
                        134,
                        0,
                        1,
                        "WAIT_FLAG",
                        120,
                        0,
                        0x218,
                        {"flag_id": 0, "target_pipe": "VEC", "consumer_pipe": "MTE2", "queue_type": "MTE2"},
                    ),
                ],
            )
            pushes, sets, waits = collect_issque_events(str(root))
            # SET_FLAG/WAIT_FLAG push events also appear in push_events (queue_type pipe),
            # matching the text-log behaviour of Push lines for SET/WAIT.
            self.assertEqual([(p.pipe, p.instr_id) for p in pushes], [("VEC", 129), ("SCALAR", 131), ("MTE2", 134)])
            self.assertEqual([(s.instr_id, s.pipe) for s in sets], [(131, "VEC")])
            self.assertEqual([(w.instr_id, w.pipe) for w in waits], [(134, "MTE2")])
            pipe_map = build_pipe_map(pushes)
            self.assertEqual(pipe_map[(0, 0, 129)], "VEC")

    def test_build_pipe_map_first_wins(self):
        evs = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=7, pc=0x10, name="X", tick=1),
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=7, pc=0x10, name="X", tick=2),
        ]
        pipe_map = build_pipe_map(evs)
        self.assertEqual(pipe_map[(0, 0, 7)], "VEC")

    def test_match_sync_flags_nearest_previous_set(self):
        sets = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=1, pc=0, name="SET_FLAG", tick=100),
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=2, pc=0, name="SET_FLAG", tick=200),
        ]
        waits = [
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=3, pc=0, name="WAIT_FLAG", tick=150),
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=4, pc=0, name="WAIT_FLAG", tick=250),
        ]
        pairs = match_sync_flags(sets, waits)
        self.assertEqual(len(pairs), 2)
        self.assertEqual([(p.set_flag_id, p.wait_flag_id) for p in pairs], [(1, 3), (2, 4)])

    def test_match_sync_flags_ignores_wait_without_prior_set(self):
        sets = [PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=1, pc=0, name="SET_FLAG", tick=100)]
        waits = [PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=2, pc=0, name="WAIT_FLAG", tick=50)]
        self.assertEqual(match_sync_flags(sets, waits), [])


class GmMemAccessDbTests(unittest.TestCase):
    def test_collect_gm_rw_events_from_db_reads_memory_type_0(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            composite_wr = 7 | (0 << 32)  # instr_id=7, core=0
            composite_rd = 9 | (1 << 32)  # instr_id=9, core=1
            _write_mem_access_db(
                root,
                [
                    (0x1000, "W", 100, 0, composite_wr, 0, 512, 1),
                    (0x2000, "R", 200, 0, composite_rd, 1, 128, 2),
                    (0x3000, "W", 300, 1, composite_wr, 0, 32, 0),  # UB rows must be excluded
                ],
            )
            gms = collect_gm_rw_events(str(root))
            self.assertEqual(
                [(g.kind, g.core, g.tick, g.port, g.instr_id, g.addr, g.size) for g in gms],
                [
                    ("WRITE", 0, 100, "mte0", 7, 0x1000, 512),
                    ("READ", 1, 200, "mte0", 9, 0x2000, 128),
                ],
            )

    def test_collect_gm_rw_events_uses_instruction_owner_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # MemoryRWAccesses.CoreId stays 0; the owning core is in the instruction row.
            _write_mem_access_db(root, [(0x1000, "W", 100, 0, 9, 0, 512, 1)])
            _write_instr_db(
                root,
                [(9 | (1 << 32), 1, 3, "MOV_SRC_TO_DST_ALIGNv2", 100, 0, 0x1234, {})],
            )
            gms = collect_gm_rw_events(str(root))
            self.assertEqual(len(gms), 1)
            self.assertEqual(gms[0].core, 1)
            self.assertEqual(gms[0].instr_id, 9)

    def test_gm_cross_core_race_uses_instruction_owner_core(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x1000, "W", 100, 0, 9, 0, 64, 0),
                    (0x1000, "W", 200, 0, 10, 0, 64, 0),
                ],
            )
            _write_instr_db(
                root,
                [
                    (9, 0, 2, "MOV_SRC_TO_DST_ALIGNv2", 100, 0, 0x100, {"queue_type": "MTE2"}),
                    (10 | (1 << 32), 1, 2, "MOV_SRC_TO_DST_ALIGNv2", 200, 0, 0x200, {"queue_type": "MTE2"}),
                ],
            )
            hits = run(str(root))
            gm_rules = [h.rule for h in hits if isinstance(h, GmViolation)]
            self.assertIn("GM_CROSS_CORE_WAW", gm_rules)

    def test_collect_gm_rw_events_no_gm_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Only UB rows (MemoryType=1), no GM rows
            _write_mem_access_db(root, [(0x3000, "W", 300, 1, 3, 0, 32, 0)])
            self.assertEqual(collect_gm_rw_events(str(root)), [])


class IntervalTests(unittest.TestCase):
    def test_overlap(self):
        self.assertTrue(Interval(0, 64).overlap(Interval(32, 96)))
        self.assertTrue(Interval(0, 64).overlap(Interval(0, 64)))
        self.assertFalse(Interval(0, 64).overlap(Interval(64, 128)))
        self.assertFalse(Interval(0, 64).overlap(Interval(100, 128)))


class WawVectorClockTests(unittest.TestCase):
    def test_is_not_happens_before(self):
        # Different pipes advance independently and cannot see each other -> concurrent
        self.assertTrue(_is_not_happens_before((0, 1, 0, 0, 0, 0, 0), (0, 0, 0, 0, 0, 1, 0), 1, 5))
        # Same pipe is serialized -> not concurrent
        self.assertFalse(_is_not_happens_before((0, 1, 0, 0, 0, 0, 0), (0, 2, 0, 0, 0, 0, 0), 1, 1))
        # Out-of-range pipe index -> not concurrent
        self.assertFalse(_is_not_happens_before((0, 1, 0, 0, 0, 0, 0), (0, 1, 0, 0, 0, 0, 0), 7, 1))


class WawCheckGmRuleTests(unittest.TestCase):
    def test_gm_pipeline_races(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x1000, "W", 1, 0, 1, 0, 512, 1),
                    (0x1000, "W", 2, 0, 2, 0, 512, 2),
                    (0x1000, "R", 3, 0, 3, 0, 512, 1),
                ],
            )
            race_hits = run(str(root))
            gm_hits = [h for h in race_hits if isinstance(h, GmViolation)]
            rules = [h.rule for h in gm_hits]
            self.assertIn("GM_WAW_OVERLAP", rules)
            self.assertIn("GM_RAW_RACE", rules)

    def test_gm_war_race(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x2000, "R", 1, 0, 1, 0, 64, 1),
                    (0x2000, "W", 2, 0, 2, 0, 64, 1),
                ],
            )
            race_hits = run(str(root))
            gm_hits = [h for h in race_hits if isinstance(h, GmViolation)]
            self.assertIn("GM_WAR_RACE", [h.rule for h in gm_hits])

    def test_gm_cross_core_waw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x5000, "W", 1, 0, 1, 0, 128, 1),
                    (0x5040, "W", 2, 0, 2, 16, 128, 1),
                ],
            )
            race_hits = run(str(root))
            gm_hits = [h for h in race_hits if isinstance(h, GmViolation)]
            rules = [h.rule for h in gm_hits]
            self.assertIn("GM_CROSS_CORE_WAW", rules)
            self.assertEqual(gm_hits[0].cores, "0,16")

    def test_detect_gm_pipeline_races_direct(self):
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x1000, size=512),
            GmEvent(kind="WRITE", core=0, tick=2, port="mte2", instr_id=2, addr=0x1000, size=512),
        ]
        hits = detect_gm_pipeline_races(evs)
        self.assertEqual([h.rule for h in hits], ["GM_WAW_OVERLAP"])

    def test_detect_gm_cross_core_races_waw_filtered(self):
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x5000, size=128),
            GmEvent(kind="WRITE", core=16, tick=2, port="mte1", instr_id=2, addr=0x5040, size=128),
        ]
        hits = [h for h in detect_gm_cross_core_races(evs) if h.rule == "GM_CROSS_CORE_WAW"]
        self.assertEqual([h.rule for h in hits], ["GM_CROSS_CORE_WAW"])

    def test_gm_cross_core_raw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x6000, "W", 1, 0, 1, 0, 128, 1),
                    (0x6040, "R", 2, 0, 2, 16, 128, 1),
                ],
            )
            race_hits = run(str(root))
            gm_hits = [h for h in race_hits if isinstance(h, GmViolation)]
            self.assertIn("GM_CROSS_CORE_RAW", [h.rule for h in gm_hits])

    def test_gm_cross_core_war(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x7000, "R", 1, 0, 1, 0, 128, 1),
                    (0x7040, "W", 2, 0, 2, 16, 128, 1),
                ],
            )
            race_hits = run(str(root))
            gm_hits = [h for h in race_hits if isinstance(h, GmViolation)]
            self.assertIn("GM_CROSS_CORE_WAR", [h.rule for h in gm_hits])

    def test_detect_gm_cross_core_races_direct(self):
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x5000, size=128),
            GmEvent(kind="WRITE", core=16, tick=2, port="mte1", instr_id=2, addr=0x5040, size=128),
            GmEvent(kind="READ", core=0, tick=3, port="mte1", instr_id=3, addr=0x5080, size=64),
            GmEvent(kind="READ", core=0, tick=4, port="mte1", instr_id=4, addr=0x50C0, size=64),
            GmEvent(kind="WRITE", core=16, tick=5, port="mte1", instr_id=5, addr=0x50C0, size=64),
        ]
        hits = detect_gm_cross_core_races(evs)
        rules = [h.rule for h in hits]
        self.assertIn("GM_CROSS_CORE_WAW", rules)
        self.assertIn("GM_CROSS_CORE_RAW", rules)
        self.assertIn("GM_CROSS_CORE_WAR", rules)

    def test_cross_core_bar_sync_suppresses(self):
        # Without an explicit cross-core sync (SET_CROSS_CORE/WAIT_FLAG_DEV) a lone
        # BAR is only an intra-core pipe barrier, not a global cross-core barrier, so
        # cross-core races must still be reported (e.g. the in-loop BAR of the
        # nosync counter-example).
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x5000, size=128),
            GmEvent(kind="WRITE", core=16, tick=3, port="mte1", instr_id=2, addr=0x5040, size=128),
        ]
        bars = [CrossCoreSyncEvent(kind="BAR", tick=2, core=0)]
        self.assertTrue(detect_gm_cross_core_races(evs, bars))
        # No sync at all -> still reported
        self.assertTrue(detect_gm_cross_core_races(evs, []))

    def test_cross_core_bar_with_sync_suppresses(self):
        # With an explicit cross-core sync (SET_CROSS_CORE) in the kernel the BAR does
        # form a global cross-core barrier; merging the cross-core vector clocks then
        # establishes happens-before and clears the race.
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x5200, size=128),
            GmEvent(kind="WRITE", core=16, tick=4, port="mte1", instr_id=2, addr=0x5240, size=128),
        ]
        bars = [
            CrossCoreSyncEvent(kind="SET_CROSS_CORE", tick=2, core=0, flag_id=7),
            CrossCoreSyncEvent(kind="BAR", tick=3, core=0),
        ]
        self.assertEqual(detect_gm_cross_core_races(evs, bars), [])

    def test_cross_core_set_wait_flag_sync_suppresses(self):
        # core0 SET_CROSS_CORE(id=5) -> core16 WAIT_FLAG_DEV(id=5): handshake clears it
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x6000, size=128),
            GmEvent(kind="WRITE", core=16, tick=4, port="mte1", instr_id=2, addr=0x6040, size=128),
        ]
        syncs = [
            CrossCoreSyncEvent(kind="SET_CROSS_CORE", tick=2, core=0, flag_id=5),
            CrossCoreSyncEvent(kind="WAIT_FLAG_DEV", tick=3, core=16, flag_id=5),
        ]
        self.assertEqual(detect_gm_cross_core_races(evs, syncs), [])
        # SET without WAIT -> no handshake -> reported
        self.assertTrue(detect_gm_cross_core_races(evs, syncs[:1]))

    def test_gm_cross_core_races_are_warning_severity(self):
        # Cross-core detection cannot confirm event/barrier sync from the BIU log, so
        # assuming no sync would be a false positive; it is downgraded to warning and
        # does not trigger a hard-failure exit code.
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x5000, size=128),
            GmEvent(kind="WRITE", core=16, tick=2, port="mte1", instr_id=2, addr=0x5040, size=128),
        ]
        hits = detect_gm_cross_core_races(evs)
        self.assertTrue(hits)
        for h in hits:
            self.assertEqual(h.severity, "warning")
        # Same-core pipeline races stay errors
        pipe_hits = detect_gm_pipeline_races(evs)
        for h in pipe_hits:
            self.assertEqual(h.severity, "error")

    def test_gm_cross_core_optimized_matches_brute_force(self):
        """Regression: the swept O(n log n + k) implementation matches the previous
        O(n^2) pairwise scan (same set and order), covering same-instr same-tick
        scheduling mirrors, same-core skips and READ x READ skips."""
        rng = random.Random(20260810)
        evs = []
        for i in range(400):
            evs.append(
                GmEvent(
                    kind=rng.choice(["WRITE", "WRITE", "READ"]),
                    core=rng.choice([0, 1, 16, 32]),
                    tick=rng.randint(1, 600),
                    port="mte1",
                    instr_id=i,
                    addr=rng.randint(0, 0x20000),
                    size=rng.choice([32, 64, 128, 256]),
                    line_no=i + 1,
                )
            )
        # Cross-core scheduling mirror with the same instr and tick (overlapping
        # address) must be skipped
        for i in range(30):
            addr = rng.randint(0, 0x20000)
            evs.append(
                GmEvent(
                    kind="WRITE",
                    core=0,
                    tick=9000,
                    port="mte1",
                    instr_id=100000 + i,
                    addr=addr,
                    size=64,
                    line_no=10000 + i,
                )
            )
            evs.append(
                GmEvent(
                    kind="WRITE",
                    core=16,
                    tick=9000,
                    port="mte1",
                    instr_id=100000 + i,
                    addr=addr,
                    size=64,
                    line_no=20000 + i,
                )
            )

        opt = _canonical_cross_core(detect_gm_cross_core_races(evs))
        ref = _brute_force_gm_cross_core(evs)
        self.assertEqual(opt, ref)
        self.assertTrue(opt)  # dense enough that several cross-core races must appear


class WawCheckSamePipeTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, kind="WRITE", name="STORE_2DV2", addr=0x1000, size=64):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name=name,
            buf_kind="ub",
        )

    def test_same_pipe_raw(self):
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(2, "VEC", 200, kind="READ", name="LOAD_2DV2")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual([h.rule for h in hits], ["SAME_PIPE_RAW"])

    def test_same_pipe_raw_beyond_instr_window_suppressed(self):
        # delta instr_id = 17 > 16 -> FIFO is strictly ordered, not reported
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(18, "VEC", 200, kind="READ", name="LOAD_2DV2")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 18): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual(hits, [])

    def test_same_pipe_raw_at_instr_window_boundary(self):
        # delta instr_id = 16 == window boundary -> still reported
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(17, "VEC", 200, kind="READ", name="LOAD_2DV2")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 17): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual([h.rule for h in hits], ["SAME_PIPE_RAW"])

    def test_same_pipe_waw_beyond_instr_window_suppressed(self):
        # delta instr_id = 20 > 16 -> WAW is not reported either
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(21, "VEC", 200, kind="WRITE", addr=0x1000)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 21): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual(hits, [])

    def test_same_pipe_war(self):
        rws = [self._rw(1, "VEC", 100, kind="READ", name="LOAD_2DV2"), self._rw(2, "VEC", 200, kind="WRITE")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual([h.rule for h in hits], ["SAME_PIPE_WAR"])

    def test_same_pipe_same_instr_burst_not_reported(self):
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(1, "VEC", 200, kind="READ", name="LOAD_2DV2")]
        pipe_map = {(0, 0, 1): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual(hits, [])

    def test_same_pipe_no_overlap(self):
        rws = [
            self._rw(1, "VEC", 100, kind="WRITE", addr=0x1000),
            self._rw(2, "VEC", 200, kind="READ", name="LOAD_2DV2", addr=0x2000),
        ]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual(hits, [])

    def test_same_pipe_waw_reported(self):
        # Same-pipe WAW is covered by SAME_PIPE_WAW (replaces rule 7 WAW_OVERWRITE)
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(2, "VEC", 200, kind="WRITE", addr=0x1000)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual([h.rule for h in hits], ["SAME_PIPE_WAW"])

    def test_same_pipe_read_consumes_write_no_waw(self):
        # An intervening READ consumes the earlier write, so same-pipe WAW stops
        rws = [
            self._rw(1, "VEC", 100, kind="WRITE"),
            self._rw(2, "VEC", 150, kind="READ", name="LOAD_2DV2"),
            self._rw(3, "VEC", 200, kind="WRITE", addr=0x1000),
        ]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC", (0, 0, 3): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        rules = [h.rule for h in hits]
        self.assertNotIn("SAME_PIPE_WAW", rules)


class WawCheckCrossPipeTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, kind="WRITE", name="STORE_2DV2", addr=0x1000, size=64):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name=name,
            buf_kind="ub",
        )

    def test_cross_pipe_waw_no_sync(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual([h.rule for h in hits], ["CROSS_PIPE_WAW_NO_SYNC"])

    def test_cross_pipe_waw_with_sync_chain(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=10,
                wait_flag_id=20,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            )
        ]
        hits = detect_cross_pipe_races(rws, pipe_map, pairs)
        self.assertEqual(hits, [])

    def test_cross_pipe_raw_no_sync(self):
        rws = [self._rw(1, "VEC", 100, kind="WRITE"), self._rw(2, "MTE3", 200, kind="READ", name="LOAD_2DV2")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual([h.rule for h in hits], ["CROSS_PIPE_RAW_NO_SYNC"])

    def test_cross_pipe_same_pipe_is_not_a_race(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "VEC", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual(hits, [])


class WawCandidateScanTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, addr=0x1000, size=64):
        return RwEvent(
            kind="WRITE",
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_scan_reports_concurrent_overlapping_writes(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        cands = _scan_kind(rws, pipe_map, [], [], "WAW")
        self.assertEqual(len(cands), 1)
        self.assertEqual((cands[0].cur_instr_id, cands[0].prev_instr_id), (2, 1))
        self.assertTrue(cands[0].is_concurrent)

    def test_scan_suppresses_synchronized_writes(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=10,
                wait_flag_id=20,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            )
        ]
        cands = _scan_kind(rws, pipe_map, pairs, [], "WAW")
        self.assertEqual(cands, [])


class RawWarCandidateScanTests(unittest.TestCase):
    """scanline+VectorClock extended to RAW/WAR (option C, no window gating)."""

    def _ev(self, kind, iid, pipe, cycle, addr=0x1000, size=64):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_raw_candidate(self):
        # WRITE(VEC) then READ(MTE3) on the same address, cross-pipe without sync -> RAW
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("READ", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        cands = _scan_kind(rws, pipe_map, [], [], "RAW")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].kind, "RAW")
        self.assertEqual((cands[0].cur_instr_id, cands[0].prev_instr_id), (2, 1))

    def test_war_candidate(self):
        # READ(VEC) then WRITE(MTE3) on the same address, cross-pipe without sync -> WAR
        rws = [self._ev("READ", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        cands = _scan_kind(rws, pipe_map, [], [], "WAR")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].kind, "WAR")
        self.assertEqual((cands[0].cur_instr_id, cands[0].prev_instr_id), (2, 1))

    def test_raw_suppressed_by_sync(self):
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("READ", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=10,
                wait_flag_id=20,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            )
        ]
        cands = _scan_kind(rws, pipe_map, pairs, [], "RAW")
        self.assertEqual(cands, [])

    def test_read_read_not_reported(self):
        rws = [self._ev("READ", 1, "VEC", 100), self._ev("READ", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        cands = _scan_kind(rws, pipe_map, [], [], "RAW")
        self.assertEqual(cands, [])

    def test_raw_war_mutually_exclusive_kinds(self):
        # The RAW and WAR entries return only their own kind and never mix
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("READ", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        self.assertEqual([c.kind for c in _scan_kind(rws, pipe_map, [], [], "RAW")], ["RAW"])
        self.assertEqual(_scan_kind(rws, pipe_map, [], [], "WAR"), [])


class WawCheckOutputTests(unittest.TestCase):
    def _violation(self, rule="CROSS_PIPE_RAW_NO_SYNC"):
        return Violation(
            rule=rule,
            kernel_id="core0.sub0",
            core=0,
            sub=0,
            pc=0xA4F0,
            instr_id=2,
            prev_instr_id=1,
            op="WRITE",
            pipe="VEC",
            buf_id=-1,
            detail="cross-pipe RAW no sync",
            severity="error",
            file="f.dump",
            line_no=2,
            raw="raw line",
            prev_pc=0xA4E0,
            prev_raw="prev raw line",
            src_file="/a/./src.asc",
            src_line=20,
            prev_src_file="/b/./prev.asc",
            prev_src_line=10,
        )

    def test_format_text_no_violations(self):
        self.assertEqual(format_text([]), "[data_race_detection] No violations found.\n")

    def test_format_text_with_pipe_violation(self):
        text = format_text([self._violation()])
        self.assertIn("Found 1 cross/same-pipe data-race violation(s)", text)
        self.assertIn("CROSS_PIPE_RAW_NO_SYNC", text)
        self.assertIn("cross-pipe RAW", text)
        self.assertIn("conflict instrs: 1→2", text)
        self.assertIn("raw line", text)
        self.assertIn("prev raw line", text)
        self.assertIn("prev src: /b/prev.asc:10", text)
        self.assertIn("src: /a/src.asc:20", text)

    def test_format_text_with_gm_violation(self):
        v = GmViolation(
            rule="GM_WAW_OVERLAP",
            core=0,
            op="WRITE",
            addr=0x1000,
            size=64,
            instr_id=2,
            instr_ids="1,2",
            port="mte1",
            tick=2,
            detail="overlap",
            file="f.dump",
            line_no=1,
        )
        text = format_text([v])
        self.assertIn("GM-layer violation(s)", text)
        self.assertIn("GM_WAW_OVERLAP", text)
        self.assertIn("GM same-core WAW", text)
        self.assertIn("conflict instrs: 1→2", text)

    def test_format_json_structure(self):
        data = json.loads(format_json([self._violation()]))
        self.assertEqual(data["race_violations"][0]["rule"], "CROSS_PIPE_RAW_NO_SYNC")
        self.assertEqual(data["race_violations"][0]["type"], "pipe")
        self.assertEqual(data["race_violations"][0]["prev_instr_id"], 1)
        self.assertEqual(data["race_violations"][0]["instr_id"], 2)

    def test_format_json_with_race_entry(self):
        c = RaceCandidate(
            core=0,
            sub=0,
            buf_kind="ub",
            cur_instr_id=2,
            cur_pc=0x20,
            cur_addr=0x1000,
            cur_size=64,
            cur_instr_name="STORE_2DV2",
            cur_pipe="MTE3",
            prev_instr_id=1,
            prev_pc=0x10,
            prev_addr=0x1000,
            prev_size=64,
            prev_instr_name="STORE_2DV2",
            prev_pipe="VEC",
            overlap_lo=0x1000,
            overlap_hi=0x1040,
            is_concurrent=True,
        )
        data = json.loads(format_json([c]))
        self.assertEqual(len(data["race_violations"]), 1)
        self.assertEqual(data["race_violations"][0]["type"], "scanline")
        self.assertTrue(data["race_violations"][0]["is_concurrent"])


class BufSyncPipesTests(unittest.TestCase):
    def test_collect_buf_sync_pipes_from_record_db(self):
        # GET_BUF/RLS_BUF come from the helper record DB -> queue_sync_pipes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (
                        1,
                        0,
                        2,
                        "GET_BUF",
                        100,
                        1,
                        0x9000D0D0E4,
                        {"sync_kind": "ACQUIRE_BUFFER", "target_pipe": "VEC", "flag_id": 3},
                    ),
                    (
                        2,
                        0,
                        2,
                        "RLS_BUF",
                        200,
                        1,
                        0x9000D0D0E8,
                        {"sync_kind": "RELEASE_BUFFER", "target_pipe": "MTE2", "flag_id": 3},
                    ),
                ],
            )
            qsp = collect_buf_sync_pipes(str(root))
            # Keys use the log-space sub id, matching rw_events (VECTOR1->sub=1,
            # VECTOR0/CUBE->sub=0)
            self.assertEqual(qsp, {(0, 0): {"VEC", "MTE2"}})

    def test_collect_buf_sync_pipes_ignores_non_sync_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (1, 0, 2, "VADD", 100, 1, 0x9000D0D0E4, {"target_pipe": "VEC"}),
                    (2, 0, 2, "VSUB", 200, 1, 0x9000D0D0E8, {"target_pipe": "MTE2"}),
                ],
            )
            self.assertEqual(collect_buf_sync_pipes(str(root)), {})

    def test_collect_buf_sync_pipes_nonexistent_path(self):
        self.assertEqual(collect_buf_sync_pipes("/nonexistent/xyz"), {})


class CrossCoreSyncParsingTests(unittest.TestCase):
    def test_collect_cross_core_sync_events_from_record_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (
                        1,
                        0,
                        1,
                        "SET_CROSS_CORE",
                        100,
                        1,
                        0x0000A4F0,
                        {"sync_kind": "SET_FLAG", "target_pipe": "MTE2", "flag_id": 5, "ffts_mode": 0},
                    ),
                    (
                        2,
                        0,
                        1,
                        "WAIT_FLAG_DEV",
                        150,
                        1,
                        0x0000A4F1,
                        {"sync_kind": "WAIT_FLAG", "target_pipe": "VEC", "flag_id": 5},
                    ),
                    (3, 0, 1, "BAR", 200, 0, 0x0000A4F2, {}),
                    (
                        4,
                        0,
                        1,
                        "WAIT_FLAG_DEVI",
                        250,
                        1,
                        0x0000A4F3,
                        {"sync_kind": "WAIT_FLAG", "target_pipe": "VEC", "flag_id": 9},
                    ),
                ],
            )
            events = collect_cross_core_sync_events(str(root))
            self.assertEqual(
                [(e.kind, e.tick, e.core, e.flag_id, e.mode) for e in events],
                [
                    ("SET_CROSS_CORE", 100, 0, 5, 0),
                    ("WAIT_FLAG_DEV", 150, 0, 5, -1),
                    ("BAR", 200, 0, -1, -1),
                    ("WAIT_FLAG_DEVI", 250, 0, 9, -1),
                ],
            )

    def test_collect_cc_sync_ignores_non_sync_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (1, 0, 1, "SET_CROSS_CORE", 100, 1, 0x0000A4F0, {"flag_id": 5}),
                    (2, 0, 1, "WAIT_FLAG_DEV", 200, 1, 0x0000A4F1, {"flag_id": 9}),
                    (3, 0, 1, "FOO", 300, 1, 0x0000A4F2, {}),
                ],
            )
            events = collect_cross_core_sync_events(str(root))
            self.assertEqual(
                [(e.kind, e.core, e.flag_id, e.mode) for e in events],
                [("SET_CROSS_CORE", 0, 5, 0), ("WAIT_FLAG_DEV", 0, 9, -1)],
            )


class IntraBlockSyncParsingTests(unittest.TestCase):
    def test_collect_intra_block_sync_events_from_record_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (1, 0, 1, "SET_INTRA_BLOCK", 100, 1, 0x9000D0D0E4, {"sync_id": 3, "target_pipe": "CUBE"}),
                    (2, 0, 2, "WAIT_INTRA_BLOCK", 150, 1, 0x9000D0D0E8, {"sync_id": 3, "target_pipe": "V"}),
                    (3, 0, 2, "VADD", 200, 1, 0x9000D0D0EC, {}),
                ],
            )
            events = collect_intra_block_sync_events(str(root))
            # sub: CUBE->0, VECTOR0->1 (the _ub_subcore_id space)
            self.assertEqual(
                [(e.kind, e.tick, e.core, e.sub, e.flag_id) for e in events],
                [("SET_INTRA_BLOCK", 100, 0, 0, 3), ("WAIT_INTRA_BLOCK", 150, 0, 1, 3)],
            )


class CrossPipeExtraTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, kind="WRITE"):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=0x1000,
            size=64,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_cross_pipe_war_no_sync(self):
        rws = [self._rw(1, "VEC", 100, kind="READ"), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual([h.rule for h in hits], ["CROSS_PIPE_WAR_NO_SYNC"])

    def test_cross_pipe_transitive_sync_chain_suppresses(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=10,
                wait_flag_id=20,
                set_pipe="VEC",
                wait_pipe="MTE1",
                set_pc=0x10,
                wait_pc=0x15,
                set_tick=100,
                wait_tick=120,
                core=0,
                sub=0,
            ),
            SyncFlagPair(
                set_flag_id=20,
                wait_flag_id=30,
                set_pipe="MTE1",
                wait_pipe="MTE3",
                set_pc=0x15,
                wait_pc=0x20,
                set_tick=130,
                wait_tick=150,
                core=0,
                sub=0,
            ),
        ]
        hits = detect_cross_pipe_races(rws, pipe_map, pairs)
        self.assertEqual(hits, [])  # the transitive VEC->MTE1->MTE3 chain counts as sync

    def test_cross_pipe_queue_sync_suppresses(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        queue_sync_pipes = {(0, 0): {"VEC"}}
        hits = detect_cross_pipe_races(rws, pipe_map, [], queue_sync_pipes=queue_sync_pipes)
        self.assertEqual(hits, [])  # exempted by the TQue queue token

    def test_cross_pipe_unknown_pipe_conflict(self):
        rws = [self._rw(1, "VEC", 100), self._rw(2, "MTE3", 200)]
        # instr2 has no named pipe -> "?" -> no cross-pipe race is judged
        pipe_map = {(0, 0, 1): "VEC"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual(hits, [])

    def test_default_pipeline_does_not_duplicate_cross_pipe_scan(self):
        # Rules 14/15/16 and ScanRaceRule detect the same class of cross-pipe
        # RAW/WAR/WAW. The default pipeline keeps only CROSS_PIPE_* so one conflicting
        # pair is not reported twice.
        evs = [
            self._rw(1, "MTE3", 100, kind="READ"),
            self._rw(2, "MTE2", 200, kind="WRITE"),
        ]
        detector = DataRaceDetector()
        ctx = RaceContext(rw_events=evs, pipe_map={(0, 0, 1): "MTE3", (0, 0, 2): "MTE2"})
        hits = [h for rule in detector._rules for h in rule.check(ctx)]
        self.assertEqual([h.rule for h in hits], ["CROSS_PIPE_WAR_NO_SYNC"])


class SamePipeExtraTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, kind="WRITE"):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=0x1000,
            size=64,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_same_pipe_unknown_pipe_skipped(self):
        rws = [
            RwEvent(
                kind="WRITE",
                core=0,
                sub=0,
                tick=1,
                pc=0x10,
                instr_id=1,
                addr=0x1000,
                size=64,
                instr_name="WEIRD_OP",
                buf_kind="ub",
            )
        ]
        hits = detect_same_pipe_races(rws, {})
        self.assertEqual(hits, [])

    def test_same_pipe_vec_same_tick_atomic_skipped(self):
        rws = [self._rw(1, "VEC", 100, kind="READ"), self._rw(2, "VEC", 100, kind="WRITE")]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "VEC"}
        hits = detect_same_pipe_races(rws, pipe_map)
        self.assertEqual(hits, [])  # an atomic same-tick R/W block is not a race


class ScanMergedTests(unittest.TestCase):
    def _ev(self, kind, iid, pipe, cycle, addr=0x1000, size=64):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_detect_scan_races_returns_all_kinds_in_one_pass(self):
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("READ", 2, "MTE3", 200), self._ev("WRITE", 3, "MTE2", 300)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3", (0, 0, 3): "MTE2"}
        cands = detect_scan_races(rws, pipe_map, [], [])
        self.assertEqual(sorted({c.kind for c in cands}), ["RAW", "WAR", "WAW"])

    def test_detect_scan_races_with_queue_sync(self):
        rws = [self._ev("WRITE", 1, "MTE2", 100), self._ev("WRITE", 2, "VEC", 200)]
        pipe_map = {(0, 0, 1): "MTE2", (0, 0, 2): "VEC"}
        queue_sync_pipes = {(0, 0): {"MTE2", "VEC"}}
        cands = detect_scan_races(rws, pipe_map, [], [], queue_sync_pipes=queue_sync_pipes)
        self.assertEqual(cands, [])  # the queue handshake makes MTE2/VEC happens-before related


class OutputExtraTests(unittest.TestCase):
    def test_format_text_with_scanline_race(self):
        c = RaceCandidate(
            core=0,
            sub=0,
            buf_kind="ub",
            cur_instr_id=2,
            cur_pc=0x20,
            cur_addr=0x1000,
            cur_size=64,
            cur_instr_name="STORE_2DV2",
            cur_pipe="MTE3",
            prev_instr_id=1,
            prev_pc=0x10,
            prev_addr=0x1000,
            prev_size=64,
            prev_instr_name="STORE_2DV2",
            prev_pipe="VEC",
            overlap_lo=0x1000,
            overlap_hi=0x1040,
            is_concurrent=True,
            kind="WAW",
            cur_vt=(0, 0, 0, 0, 1, 0, 0),
            prev_vt=(0, 1, 0, 0, 0, 0, 0),
            file="f.dump",
            line_no=2,
            raw="cur raw",
            prev_raw="prev raw",
        )
        text = format_text([c])
        self.assertIn("scanline+VectorClock", text)
        self.assertIn("prev raw", text)
        self.assertIn("cur  raw", text)


class MiscCoverageTests(unittest.TestCase):
    def test_infer_pipe_from_instr_name(self):
        self.assertEqual(infer_pipe_from_instr_name("RV_VLD"), "VEC")
        self.assertEqual(infer_pipe_from_instr_name("RV_VST"), "VEC")
        self.assertEqual(infer_pipe_from_instr_name("VADD"), "VEC")
        self.assertEqual(infer_pipe_from_instr_name("AND_I2V"), "VEC")
        self.assertEqual(infer_pipe_from_instr_name("FOOBAR"), "?")

    def test_collect_single_file_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(root, [(0x1000, "R", 100, 1, 2, 0, 32, 0)])
            rws = collect_buffer_rw_events(str(root))
            self.assertEqual(len(rws), 1)

            gm_db = root / "gm"
            gm_db.mkdir()
            _write_mem_access_db(gm_db, [(0x3000, "R", 1, 0, 3, 0, 128, 1)])
            gms = collect_gm_rw_events(str(gm_db))
            self.assertEqual([(g.kind, g.port) for g in gms], [("READ", "mte0")])

            _write_instr_db(
                root,
                [(129, 0, 2, "STORE_2DV2", 100, 0, 0x204, {"queue_type": "SIMD"})],
            )
            pushes, sets, waits = collect_issque_events(str(root))
            self.assertEqual([p.instr_id for p in pushes], [129])
            self.assertEqual(sets, [])
            self.assertEqual(waits, [])

    def test_collect_invalid_input_path(self):
        self.assertEqual(collect_buffer_rw_events("/nonexistent/xyz"), [])
        self.assertEqual(collect_gm_rw_events("/nonexistent/xyz"), [])
        self.assertEqual(collect_buf_sync_pipes("/nonexistent/xyz"), {})
        pushes, sets, waits = collect_issque_events("/nonexistent/xyz")
        self.assertEqual((pushes, sets, waits), ([], [], []))


class ParseEdgeTests(unittest.TestCase):
    def test_collect_buffer_rw_events_sqlite_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(root, [(0x1000, "W", 150, 1, 2, 0, 32, 0)])
            with patch("sqlite3.connect", side_effect=sqlite3.Error):
                self.assertEqual(collect_buffer_rw_events(str(root)), [])

    def test_collect_buf_sync_pipes_sqlite_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(root, [(1, 0, 2, "GET_BUF", 100, 1, 0x9000D0D0E4, {"target_pipe": "VEC"})])
            with patch("sqlite3.connect", side_effect=sqlite3.Error):
                self.assertEqual(collect_buf_sync_pipes(str(root)), {})

    def test_collect_issque_events_sqlite_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [(129, 0, 2, "STORE_2DV2", 100, 0, 0x204, {"queue_type": "SIMD"})],
            )
            with patch("sqlite3.connect", side_effect=sqlite3.Error):
                pushes, sets, waits = collect_issque_events(str(root))
            self.assertEqual((pushes, sets, waits), ([], [], []))


class ProgramOrderIndexTests(unittest.TestCase):
    def _ev(self, kind, iid, pipe, cycle, addr=0x1000, size=64, name="STORE_2DV2"):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name=name,
            buf_kind="ub",
        )

    def test_program_order_uses_push_tick_map(self):
        # Every event instr is in push_events -> use the Push tick (index hit)
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pushes = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=1, pc=0x10, name="X", tick=50),
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=2, pc=0x20, name="X", tick=60),
        ]
        cands = _scan_kind(rws, pipe_map, [], pushes, "WAW")
        self.assertEqual(len(cands), 1)

    def test_program_order_pc_fallback_for_micro_ops(self):
        # The uop (RV_VLD) is not in push_events -> use the largest same-pipe Push tick
        # with PC <= ev.pc (sentinel excluded)
        rw = self._ev("WRITE", 1, "VEC", 100, name="RV_VLD")
        pushes = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=99, pc=0x800, name="P", tick=40),
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=98, pc=0x1200, name="P", tick=90),
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=97, pc=0x11111111, name="SENT", tick=95),
        ]
        cands = _scan_kind([rw], {}, [], pushes, "WAW")
        self.assertEqual(cands, [])  # a single event has no candidates but covers the PC
        # fallback and sentinel exclusion

    def test_scan_skips_unknown_pipe_events(self):
        rw = self._ev("WRITE", 5, "?", 1, name="FOOBAR")
        cands = _scan_kind([rw], {}, [], [], "WAW")
        self.assertEqual(cands, [])

    def test_sync_order_tick_program_order_fallback(self):
        # The SET/WAIT flag instr is not in push_events -> use the largest same-pipe
        # Push tick with PC <= flag PC
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pushes = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=99, pc=0x10, name="X", tick=80),
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=98, pc=0x20, name="X", tick=90),
        ]
        pairs = [
            SyncFlagPair(
                set_flag_id=50,
                wait_flag_id=60,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            )
        ]
        cands = _scan_kind(rws, pipe_map, pairs, pushes, "WAW")
        self.assertEqual(len(cands), 1)  # a normal SET/WAIT pair does not suppress concurrency

    def test_sync_order_tick_uses_push_map_for_flags(self):
        # The flag instr itself is in push_events -> SET/WAIT use that Push tick
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pushes = [
            PipeIssEvent(core=0, sub=0, pipe="VEC", instr_id=50, pc=0x10, name="SET_FLAG", tick=80),
            PipeIssEvent(core=0, sub=0, pipe="MTE3", instr_id=60, pc=0x20, name="WAIT_FLAG", tick=90),
        ]
        pairs = [
            SyncFlagPair(
                set_flag_id=50,
                wait_flag_id=60,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            )
        ]
        cands = _scan_kind(rws, pipe_map, pairs, pushes, "WAW")
        self.assertEqual(len(cands), 1)

    def test_vc_skips_unknown_sync_pipes(self):
        # The SET/WAIT pipe is unknown (no PIPE_IDX index) -> the sync signal is
        # skipped and does not suppress
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=50,
                wait_flag_id=60,
                set_pipe="XYZ",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            ),
            SyncFlagPair(
                set_flag_id=70,
                wait_flag_id=80,
                set_pipe="VEC",
                wait_pipe="XYZ",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=100,
                wait_tick=150,
                core=0,
                sub=0,
            ),
        ]
        cands = _scan_kind(rws, pipe_map, pairs, [], "WAW")
        self.assertEqual(len(cands), 1)


class ScanGuardBranchTests(unittest.TestCase):
    def _ev(self, kind, iid, pipe, cycle, addr=0x1000, size=64):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=size,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_scan_guard_skips(self):
        # Split/duplicate the same instr (731) and same pipe does not count (746);
        # 735 is the sweep guard
        rws = [
            self._ev("WRITE", 1, "VEC", 100),
            self._ev("WRITE", 1, "VEC", 110),
            self._ev("WRITE", 2, "MTE3", 200, addr=0x3000),
            self._ev("WRITE", 3, "VEC", 210, addr=0x1000, size=64),
        ]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3", (0, 0, 3): "VEC"}
        cands = _scan_kind(rws, pipe_map, [], [], "WAW")
        self.assertEqual(cands, [])

    def test_scan_wait_without_set_signal(self):
        # The WAIT arrives before the SET -> no signal snapshot -> no suppression
        rws = [self._ev("WRITE", 1, "VEC", 100), self._ev("WRITE", 2, "MTE3", 200)]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        pairs = [
            SyncFlagPair(
                set_flag_id=50,
                wait_flag_id=60,
                set_pipe="VEC",
                wait_pipe="MTE3",
                set_pc=0x10,
                wait_pc=0x20,
                set_tick=300,
                wait_tick=100,
                core=0,
                sub=0,
            )
        ]
        cands = _scan_kind(rws, pipe_map, pairs, [], "WAW")
        self.assertEqual(len(cands), 1)


class CrossPipeSkipBranchTests(unittest.TestCase):
    def _rw(self, iid, pipe, cycle, kind="WRITE", addr=0x1000):
        return RwEvent(
            kind=kind,
            core=0,
            sub=0,
            tick=cycle,
            pc=0x1000 + iid,
            instr_id=iid,
            addr=addr,
            size=64,
            instr_name="STORE_2DV2",
            buf_kind="ub",
        )

    def test_cross_pipe_write_skip_branches(self):
        rws = [
            self._rw(1, "VEC", 100),
            self._rw(1, "MTE3", 200),
            self._rw(1, "VEC", 150, kind="READ", addr=0x5000),
            self._rw(2, "MTE3", 300, addr=0x9000),
        ]
        pipe_map = {(0, 0, 1): "VEC", (0, 0, 2): "MTE3"}
        hits = detect_cross_pipe_races(rws, pipe_map, [])
        self.assertEqual(hits, [])  # skipped for the same instr / for no overlap


class GmExtraTests(unittest.TestCase):
    def test_gm_waw_consumed_by_read_not_reported(self):
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x1000, size=64),
            GmEvent(kind="READ", core=0, tick=2, port="mte1", instr_id=1, addr=0x1000, size=64),
            GmEvent(kind="WRITE", core=0, tick=3, port="mte1", instr_id=2, addr=0x1000, size=64),
        ]
        hits = detect_gm_pipeline_races(evs)
        rules = [h.rule for h in hits]
        self.assertNotIn("GM_WAW_OVERLAP", rules)  # W1 was consumed by a same-instr READ
        self.assertIn("GM_WAR_RACE", rules)  # R1 -> W2 still forms a WAR

    def test_gm_cross_core_boundary_no_overlap(self):
        evs = [
            GmEvent(kind="WRITE", core=0, tick=1, port="mte1", instr_id=1, addr=0x1000, size=64),
            GmEvent(kind="WRITE", core=16, tick=2, port="mte1", instr_id=2, addr=0x1040, size=64),
        ]
        self.assertEqual(detect_gm_cross_core_races(evs), [])

    def test_gm_cross_core_empty(self):
        self.assertEqual(detect_gm_cross_core_races([]), [])


class OutputExtra2Tests(unittest.TestCase):
    def test_format_text_scanline_aggregates_many_same_instr(self):
        # Repeated races for one (cur_instr_id, prev_instr_id) aggregate into a single
        # xN line with merged address ranges
        base = dict(
            core=0,
            sub=0,
            buf_kind="ub",
            cur_instr_id=2,
            cur_pc=0x20,
            cur_addr=0x1000,
            cur_size=64,
            cur_instr_name="STORE_2DV2",
            cur_pipe="MTE3",
            prev_instr_id=1,
            prev_pc=0x10,
            prev_addr=0x1000,
            prev_size=64,
            prev_instr_name="STORE_2DV2",
            prev_pipe="VEC",
            overlap_lo=0x1000,
            overlap_hi=0x1040,
            is_concurrent=True,
            kind="WAW",
        )
        cands = [RaceCandidate(**base) for _ in range(31)]
        text = format_text(cands)
        self.assertIn("conflict instrs: 1→2 x31", text)
        self.assertIn("range=[0x1000,0x1040) (1 segments, 31 hits)", text)
        self.assertIn("cross-pipe WAW", text)
        self.assertNotIn("sub0", text)  # sub naming moved to cube0/vector0
        self.assertIn("core0.cube0.ub", text)

    def test_format_json_with_gm_violation(self):
        v = GmViolation(
            rule="GM_CROSS_CORE_WAW",
            core=0,
            cores="0,16",
            op="WRITE",
            addr=0x1000,
            size=64,
            instr_id=1,
            instr_ids="1,2",
            port="mte1",
            tick=2,
            detail="overlap",
            file="f.dump",
            line_no=1,
            raw="gm raw",
        )
        data = json.loads(format_json([v]))
        self.assertEqual(data["race_violations"][0]["type"], "gm")
        self.assertIn("gm raw", format_text([v]))


def _ub_rw_event(kind, core, sub, tick, instr_id, addr, size, buf_kind="ub"):
    # The file name must contain cubecore/veccore so _ub_subcore_id can infer the type
    sub_names = {0: "cubecore0", 1: "veccore0", 2: "veccore1"}
    fname = f"core{core}.{sub_names.get(sub, f'sub{sub}')}.ub.{'wr' if kind == 'WRITE' else 'rd'}_log.dump"
    return RwEvent(
        kind=kind,
        core=core,
        sub=sub,
        tick=tick,
        pc=0x1000,
        instr_id=instr_id,
        addr=addr,
        size=size,
        instr_name="TEST_OP",
        buf_kind=buf_kind,
        file=fname,
        line_no=tick,
        file_order=tick,
        raw=f"test {kind} core{core} sub{sub} id={instr_id}",
    )


def _intra_sync(kind, tick, core, sub, flag_id):
    return IntraBlockSyncEvent(kind=kind, tick=tick, core=core, sub=sub, flag_id=flag_id)


class UbCrossCoreTests(unittest.TestCase):
    """UB cross-subcore detection: on Ascend950 the cubecore and veccore share UB."""

    def _rules(self, hits):
        return [h.rule for h in hits]

    def test_ub_cross_core_waw_no_sync(self):
        # cubecore WRITE + veccore WRITE on the same address, no SET_INTRA_BLOCK -> WAW
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),  # cubecore write
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),  # veccore0 write
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertIn("UB_CROSS_CORE_WAW", self._rules(hits))

    def test_ub_cross_core_raw_no_sync(self):
        # cubecore WRITE + veccore READ on the same address, no sync -> RAW
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("READ", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertIn("UB_CROSS_CORE_RAW", self._rules(hits))

    def test_ub_cross_core_war_no_sync(self):
        # cubecore READ + veccore WRITE on the same address, no sync -> WAR
        evs = [
            _ub_rw_event("READ", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertIn("UB_CROSS_CORE_WAR", self._rules(hits))

    def test_ub_cross_core_waw_with_sync_no_violation(self):
        # cubecore WRITE + SET_INTRA_BLOCK + veccore WAIT_INTRA_BLOCK + veccore WRITE
        # -> synchronized, no violation
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
        ]
        syncs = [
            _intra_sync("SET_INTRA_BLOCK", 105, 0, 0, 3),  # cubecore sets
            _intra_sync("WAIT_INTRA_BLOCK", 108, 0, 1, 3),  # veccore0 waits
        ]
        evs.append(_ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64))
        hits = detect_ub_cross_core_races(evs, syncs)
        self.assertNotIn("UB_CROSS_CORE_WAW", self._rules(hits))

    def test_ub_cross_core_same_sub_no_violation(self):
        # A race inside one sub is not handled here (same-pipe/cross-pipe does it)
        evs = [
            _ub_rw_event("WRITE", 0, 1, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertEqual(hits, [])

    def test_ub_cross_core_no_overlap_no_violation(self):
        # Different subs and different addresses -> no violation
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x9000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertEqual(hits, [])

    def test_ub_cross_core_different_block_no_violation(self):
        # UB is not shared across blocks (cores) -> no violation
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 16, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertEqual(hits, [])

    def test_ub_cross_core_read_read_no_violation(self):
        # READ x READ is not a data race
        evs = [
            _ub_rw_event("READ", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("READ", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertEqual(hits, [])

    def test_ub_cross_core_non_ub_filtered(self):
        # A non-UB buffer (l0c) does not take part in UB cross-subcore detection
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64, buf_kind="l0c"),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64, buf_kind="l0c"),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertEqual(hits, [])

    def test_ub_cross_core_partial_overlap(self):
        # Partial overlap is reported too
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8020, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        self.assertIn("UB_CROSS_CORE_WAW", self._rules(hits))

    def test_ub_cross_core_three_subcore(self):
        # cubecore + veccore0 + veccore1 all write the same address -> 2 WAWs
        # (sub0 x sub1, sub0 x sub2)
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),  # cubecore
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),  # veccore0
            _ub_rw_event("WRITE", 0, 2, 120, 3, 0x8000, 64),  # veccore1
        ]
        hits = detect_ub_cross_core_races(evs, [])
        rules = self._rules(hits)
        self.assertEqual(rules.count("UB_CROSS_CORE_WAW"), 2)

    def test_ub_cross_core_vt_map_built(self):
        # Verify the vector clock establishes happens-before
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("READ", 0, 1, 110, 2, 0x8000, 64),
        ]
        syncs = [
            _intra_sync("SET_INTRA_BLOCK", 105, 0, 0, 5),
            _intra_sync("WAIT_INTRA_BLOCK", 108, 0, 1, 5),
        ]
        vt_map = build_ub_cross_core_vc(evs, syncs)
        # cubecore event (core=0, sub=0, instr_id=1) should have VC
        self.assertIn((0, 0, 1), vt_map)
        # veccore0 event (core=0, sub=1, instr_id=2) should have VC
        self.assertIn((0, 1, 2), vt_map)
        # After SET+WAIT, veccore0 VC should >= cubecore VC (happens-before)
        earlier_vt = vt_map[(0, 0, 1)]
        later_vt = vt_map[(0, 1, 2)]
        self.assertTrue(all(a <= b for a, b in zip(earlier_vt, later_vt)))

    def test_ub_cross_core_format_text(self):
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        text = format_text(hits)
        self.assertIn("UB cross-subcore", text)
        self.assertIn("UB_CROSS_CORE_WAW", text)

    def test_ub_cross_core_format_json(self):
        evs = [
            _ub_rw_event("WRITE", 0, 0, 100, 1, 0x8000, 64),
            _ub_rw_event("WRITE", 0, 1, 110, 2, 0x8000, 64),
        ]
        hits = detect_ub_cross_core_races(evs, [])
        data = json.loads(format_json(hits))
        self.assertEqual(data["race_violations"][0]["type"], "ub_cross_core")
        self.assertEqual(data["race_violations"][0]["rule"], "UB_CROSS_CORE_WAW")


class UbCrossCoreIntegrationTests(unittest.TestCase):
    """End-to-end tests of UB cross-subcore detection through run()."""

    def test_run_detects_ub_cross_core_waw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # cubecore UB write (CoreTypeId=1) + veccore0 UB write (CoreTypeId=2),
            # same address, no sync
            _write_mem_access_db(
                root,
                [
                    (0x8000, "W", 100, 1, 1, 0, 64, 0),
                    (0x8000, "W", 110, 1, 2, 0, 64, 0),
                ],
            )
            _write_instr_db(
                root,
                [
                    (1, 0, 1, "STORE_2DV2", 100, 0, 0x100, {"queue_type": "CUBE"}),
                    (2, 0, 2, "STORE_2DV2", 110, 0, 0x108, {"queue_type": "SIMD"}),
                ],
            )
            hits = run(str(root))
            rules = [h.rule for h in hits if isinstance(h, GmViolation) and h.rule.startswith("UB_")]
            self.assertIn("UB_CROSS_CORE_WAW", rules)

    def test_run_ub_cross_core_with_sync_no_violation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # cubecore UB write + veccore0 UB write (MemoryType=1), with SET/WAIT_INTRA_BLOCK sync
            _write_mem_access_db(
                root,
                [
                    (0x8000, "W", 100, 1, 1, 0, 64, 0),
                    (0x8000, "W", 110, 1, 2, 0, 64, 0),
                ],
            )
            _write_instr_db(
                root,
                [
                    (1, 0, 1, "STORE_2DV2", 100, 0, 0x100, {"queue_type": "CUBE"}),
                    (2, 0, 2, "STORE_2DV2", 110, 0, 0x108, {"queue_type": "SIMD"}),
                    (3, 0, 1, "SET_INTRA_BLOCK", 105, 1, 0x9000D0D0E4, {"sync_id": 3, "target_pipe": "CUBE"}),
                    (4, 0, 2, "WAIT_INTRA_BLOCK", 108, 1, 0x9000D0D0F8, {"sync_id": 3, "target_pipe": "V"}),
                ],
            )
            hits = run(str(root))
            ub_rules = [h.rule for h in hits if isinstance(h, GmViolation) and h.rule.startswith("UB_")]
            self.assertNotIn("UB_CROSS_CORE_WAW", ub_rules)


class KernelIsolationTests(unittest.TestCase):
    """Each record DB is one kernel launch; race state must not leak across kernels."""

    def test_same_gm_address_written_in_two_kernels_is_not_waw(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [(0x1000, "W", 100, 0, 1, 0, 64, 0)],
                filename="kernel_a.db",
            )
            _write_mem_access_db(
                root,
                [(0x1000, "W", 100, 0, 1, 0, 64, 0)],
                filename="kernel_b.db",
            )
            hits = run(str(root))
            gm_rules = [h.rule for h in hits if isinstance(h, GmViolation)]
            self.assertNotIn("GM_WAW_OVERLAP", gm_rules)
            self.assertNotIn("GM_CROSS_CORE_WAW", gm_rules)

    def test_gm_waw_within_one_kernel_still_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mem_access_db(
                root,
                [
                    (0x1000, "W", 100, 0, 1, 0, 64, 0),
                    (0x1000, "W", 200, 0, 2, 0, 64, 0),
                ],
                filename="kernel_a.db",
            )
            hits = run(str(root))
            gm_rules = [h.rule for h in hits if isinstance(h, GmViolation)]
            self.assertIn("GM_WAW_OVERLAP", gm_rules)


if __name__ == "__main__":
    unittest.main()
