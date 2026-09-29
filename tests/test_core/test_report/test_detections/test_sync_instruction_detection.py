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

"""Unit tests for npusim.core.report.detections.sync_instruction_detection.

The check reads instruction facts from helper record DB and validates
SET_FLAG / WAIT_FLAG, intra-block, cross-core, buffer, and mix-mode rules.
"""

import json
import sqlite3
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path

from npusim.core.report.detections.sync_instruction_detection import (
    CrossCoreSyncContainer,
    Violation,
    collect_events,
    core_kind,
    format_json,
    format_text,
    precompute_rls_count,
    SyncEvent,
    run,
)

_InstrRow = namedtuple("_InstrRow", "instr_id core_id core_type_id op tick instr_type_id pc params")
RecordRow = namedtuple("RecordRow", "instr_id core_id core_type_id op tick is_sync pc params")


def _write_record_db(dirpath, rows):
    """Create a helper record DB containing executed instructions."""
    path = Path(dirpath) / "record.db"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE ExecutedInstructions(
                ExecInstrId INTEGER PRIMARY KEY,
                ExecInstrCoreId INTEGER,
                CoreTypeId INTEGER,
                ExecInstrName TEXT,
                ExecInstrTickStart INTEGER,
                InstrTypeId INTEGER,
                SourceInstrAddr INTEGER,
                ExtendParams TEXT);
            """
        )
        con.executemany(
            "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.instr_id,
                    row.core_id,
                    row.core_type_id,
                    row.op,
                    row.tick,
                    row.instr_type_id,
                    row.pc,
                    json.dumps(row.params),
                )
                for row in (_InstrRow(*row) for row in rows)
            ],
        )


class SyncCheckParseTests(unittest.TestCase):
    def test_collect_events_from_record_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (
                        1,
                        0,
                        2,
                        "SET_FLAG",
                        100,
                        0,
                        0x9000D0D0E4,
                        {"is_sync": True, "target_pipe": "SCALAR", "consumer_pipe": "VEC", "flag_id": 5},
                    ),
                    (
                        2,
                        0,
                        2,
                        "WAIT_FLAG",
                        110,
                        0,
                        0x9000D0D0E8,
                        {"is_sync": True, "target_pipe": "VEC", "consumer_pipe": "SCALAR", "flag_id": 5},
                    ),
                ],
            )
            evs = collect_events(str(root))
            self.assertEqual(len(evs), 2)
            self.assertEqual([e.cycle for e in evs], [100, 110])
            self.assertEqual(evs[0].core, "core0.vector0")
            self.assertEqual(evs[0].pc, 0x9000D0D0E4)
            self.assertEqual(evs[0].instr_id, 1)

    def test_non_sync_instruction_is_excluded_from_sync_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(
                root,
                [
                    (
                        1,
                        0,
                        2,
                        "VADD",
                        100,
                        1,
                        0x9000D0D0E4,
                        {"is_sync": False, "instr_type": "VEC"},
                    ),
                    (
                        2,
                        0,
                        2,
                        "SET_FLAG",
                        101,
                        0,
                        0x9000D0D0E8,
                        {"is_sync": True, "target_pipe": "SCALAR", "consumer_pipe": "VEC", "flag_id": 5},
                    ),
                ],
            )
            evs = collect_events(str(root))
            self.assertEqual([ev.op for ev in evs], ["SET_FLAG"])

    def test_core_kind(self):
        self.assertEqual(core_kind("core0.veccore0"), "VEC0")
        self.assertEqual(core_kind("core0.vec_core0"), "VEC0")
        self.assertEqual(core_kind("core0.veccore1"), "VEC1")
        # mix-mode: VECCORE 后缀数字直接决定 VEC0/VEC1，与 cid 无关
        self.assertEqual(core_kind("core16.veccore0"), "VEC0")
        self.assertEqual(core_kind("core16.veccore1"), "VEC1")
        self.assertEqual(core_kind("core0.cubecore0"), "CUBE")
        self.assertEqual(core_kind("core0.cube0"), "CUBE")
        self.assertEqual(core_kind("core0.unknown0"), "UNKNOWN")


class SyncCheckRuleTests(unittest.TestCase):
    """End-to-end rule detection via run() on synthetic record DBs."""

    @staticmethod
    def _rows(
        op,
        tick,
        instr_id=1,
        instr_type_id=0,
        params=None,
        core_id=0,
        core_type_id=2,
    ):
        defaults = {"is_sync": True, "target_pipe": "SCALAR", "consumer_pipe": "VEC", "flag_id": 5}
        return [
            (
                instr_id,
                core_id,
                core_type_id,
                op,
                tick,
                instr_type_id,
                0x9000D0D0E4 + instr_id * 4,
                dict(defaults, **(params or {})),
            )
        ]

    def _run_db(self, rows, mix_mode_force=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(root, rows)
            return run(str(root), mix_mode_force=mix_mode_force)

    def _rules(self, hits):
        return [h.rule for h in hits]

    def test_clean_balanced_log_has_no_violations(self):
        rows = self._rows("SET_FLAG", 100) + self._rows("WAIT_FLAG", 110, 2)
        rows += self._rows("SET_INTRA_BLOCK", 120, 3, params={"target_pipe": "V", "sync_id": 3})
        rows += self._rows("WAIT_INTRA_BLOCK", 130, 4, params={"target_pipe": "V", "sync_id": 3})
        hits = self._run_db(rows)
        self.assertEqual(hits, [])

    def test_flag_id_oob(self):
        hits = self._run_db(self._rows("SET_FLAG", 100, params={"flag_id": 16}))
        self.assertIn("FLAG_ID_OOB", self._rules(hits))

    def test_dup_set_unconsumed(self):
        rows = self._rows("SET_FLAG", 100) + self._rows("SET_FLAG", 101, 2)
        hits = self._run_db(rows)
        self.assertIn("DUP_SET_UNCONSUMED", self._rules(hits))

    def test_wait_no_arrive(self):
        hits = self._run_db(self._rows("WAIT_FLAG", 100))
        self.assertIn("WAIT_NO_ARRIVE", self._rules(hits))

    def test_wait_overconsume(self):
        rows = self._rows("SET_FLAG", 100) + self._rows("WAIT_FLAG", 110, 2) + self._rows("WAIT_FLAG", 120, 3)
        hits = self._run_db(rows)
        self.assertIn("WAIT_OVERCONSUME", self._rules(hits))
        self.assertNotIn("WAIT_NO_ARRIVE", self._rules(hits))

    def test_unconsumed_arrive_at_kernel_end(self):
        hits = self._run_db(self._rows("SET_FLAG", 100))
        rules = self._rules(hits)
        self.assertIn("UNCONSUMED_ARRIVE", rules)
        self.assertNotIn("DUP_SET_UNCONSUMED", rules)

    def test_wait_flag_dev_without_set_cross_core(self):
        hits = self._run_db(self._rows("WAIT_FLAG_DEV", 100, params={"target_pipe": "VEC", "flag_id": 1}))
        self.assertIn("WAIT_DEV_NO_SET", self._rules(hits))

    def test_cross_core_set_unconsumed_at_end(self):
        hits = self._run_db(self._rows("SET_CROSS_CORE", 100, params={"target_pipe": "MTE3", "flag_id": 1}))
        self.assertIn("UNCONSUMED_CROSS_CORE", self._rules(hits))

    def test_mode0_vec_set_is_dispatched_once_to_both_vector_queues(self):
        """MODE0 VEC 广播只应产生一个事件，并被两个 VEC WAIT 队列共享。"""
        container = CrossCoreSyncContainer()
        ev = SyncEvent(
            op="SET_CROSS_CORE",
            core="core0.vector0",
            pc=0x1000,
            instr_id=1,
            cycle=10,
            flag_id=3,
        )
        container._set_mode0(3, ev.core, ev)

        set_events = container.pending_sets()
        wait_events = container.pending_waits()
        self.assertEqual([item[-1] for item in set_events], [ev])
        self.assertEqual([item[-1] for item in wait_events], [ev, ev])

        consumed = [
            container.get_block_sync_info(3, "core0.vector0"),
            container.get_block_sync_info(3, "core0.vector1"),
        ]
        self.assertEqual(consumed, [ev, ev])
        self.assertEqual(container.pending_waits(), [])

    def test_rls_buf_overconsume(self):
        rows = self._rows("GET_BUF", 100, params={"target_pipe": "VEC", "flag_id": 2})
        rows += self._rows("VADD", 150, 10, 1, {"instr_type": "VEC", "is_sync": False})
        rows += self._rows("GET_BUF", 200, 2, params={"target_pipe": "VEC", "flag_id": 2})
        hits = self._run_db(rows)
        rules = self._rules(hits)
        self.assertIn("RLS_BUF_OVERCONSUME", rules)
        self.assertIn("BUF_STALL", rules)

    def test_block_wait_premature(self):
        hits = self._run_db(self._rows("WAIT_INTRA_BLOCK", 100, params={"target_pipe": "V", "sync_id": 3}))
        self.assertIn("BLOCK_WAIT_PREMATURE", self._rules(hits))

    def test_intra_block_set_wait_balanced(self):
        rows = self._rows("SET_INTRA_BLOCK", 100, params={"target_pipe": "V", "sync_id": 3})
        rows += self._rows("WAIT_INTRA_BLOCK", 110, 2, params={"target_pipe": "V", "sync_id": 3})
        hits = self._run_db(rows)
        self.assertEqual(hits, [])


class SyncCheckLockBufTests(unittest.TestCase):
    """GET_BUF/RLS_BUF buf sync detection (stall + overconsume + id oob)."""

    @staticmethod
    def _row(op, tick, instr_id, flag_id=3, pipe="VEC", is_sync=True, instr_type="VEC"):
        return RecordRow(
            instr_id,
            0,
            2,
            op,
            tick,
            1 if pipe == "VEC" else 0,
            0x9000D0D0E4 + instr_id * 4,
            {
                "sync_kind": op if is_sync else "",
                "instr_type": instr_type,
                "target_pipe": pipe,
                "flag_id": flag_id,
            },
        )

    def _rules(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(root, rows)
            return [h.rule for h in run(str(root))]

    def test_buf_id_oob(self):
        rules = self._rules([self._row("GET_BUF", 100, 1, flag_id=16)])
        self.assertIn("BUF_ID_OOB", rules)

    def test_balanced_get_rls_no_violation(self):
        rows = [self._row("RLS_BUF", 50, 1), self._row("GET_BUF", 100, 2)]
        rules = self._rules(rows)
        self.assertNotIn("BUF_STALL", rules)
        self.assertNotIn("RLS_BUF_OVERCONSUME", rules)

    def test_buf_stall_consecutive_get_no_rls(self):
        rows = [
            self._row("GET_BUF", 100, 1),
            self._row("VADD", 150, 10, is_sync=False),
            self._row("GET_BUF", 200, 2),
        ]
        rules = self._rules(rows)
        self.assertIn("BUF_STALL", rules)

    def test_buf_stall_get_before_rls_arrives(self):
        rows = [self._row("GET_BUF", 100, 1), self._row("GET_BUF", 200, 2), self._row("RLS_BUF", 300, 3)]
        rules = self._rules(rows)
        self.assertIn("BUF_STALL", rules)

    def test_buf_stall_rls_then_two_gets_balanced(self):
        rows = [
            self._row("RLS_BUF", 50, 1),
            self._row("GET_BUF", 100, 2),
            self._row("VADD", 150, 10, is_sync=False),
            self._row("GET_BUF", 200, 3),
        ]
        rules = self._rules(rows)
        self.assertNotIn("BUF_STALL", rules)

    def test_rls_count_precomputation(self):
        events = [
            self._buf_event("GET_BUF", 100, 1, 3),
            self._buf_event("RLS_BUF", 200, 2, 3),
            self._buf_event("RLS_BUF", 300, 3, 3),
            self._buf_event("GET_BUF", 400, 4, 3),
            self._buf_event("RLS_BUF", 500, 5, 5),
        ]
        precompute_rls_count(events)
        get_bufs = [e for e in events if e.op == "GET_BUF"]
        self.assertEqual(get_bufs[0].rls_count, 2)
        self.assertEqual(get_bufs[1].rls_count, 2)

    @staticmethod
    def _buf_event(op: str, cycle: int, instr_id: int, flag_id: int) -> SyncEvent:
        return SyncEvent(
            op=op,
            core="core0.veccore0",
            pc=0,
            instr_id=instr_id,
            cycle=cycle,
            pipe="VEC",
            flag_id=flag_id,
        )


class SyncCheckMixModeTests(unittest.TestCase):
    """Intra-block synchronization rules across cube and vector cores."""

    @staticmethod
    def _row(op, tick, instr_id, sync_id, core_id=0, core_type_id=2):
        return RecordRow(
            instr_id,
            core_id,
            core_type_id,
            op,
            tick,
            0,
            0x9000D0D0E4 + instr_id,
            {
                "sync_kind": op,
                "target_pipe": "VEC" if core_type_id == 2 else "CUBE",
                "sync_id": sync_id,
            },
        )

    def _run_db(self, rows, mix_mode_force=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(root, rows)
            return run(str(root), mix_mode_force=mix_mode_force)

    def _rules(self, hits):
        return [hit.rule for hit in hits]

    def test_balanced_vector_sets_and_cube_waits(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 3),
            self._row("SET_INTRA_BLOCK", 11, 2, 3, core_type_id=3),
            self._row("WAIT_INTRA_BLOCK", 12, 3, 3, core_type_id=1),
            self._row("WAIT_INTRA_BLOCK", 13, 4, 19, core_type_id=1),
        ]
        self.assertEqual(self._run_db(rows), [])

    def test_unconsumed_set(self):
        rows = [self._row("SET_INTRA_BLOCK", 10, 1, 3)]
        self.assertIn("MIX_MODE_UNCONSUMED", self._rules(self._run_db(rows)))

    def test_wait_no_arrive(self):
        rows = [self._row("WAIT_INTRA_BLOCK", 10, 1, 3, core_type_id=1)]
        self.assertIn("MIX_MODE_WAIT_NO_ARRIVE", self._rules(self._run_db(rows)))

    def test_vector1_set_requires_cube_wait_plus_sixteen(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 3, core_type_id=3),
            self._row("WAIT_INTRA_BLOCK", 12, 2, 3, core_type_id=1),
        ]
        rules = self._rules(self._run_db(rows))
        self.assertIn("MIX_MODE_WAIT_NO_ARRIVE", rules)
        self.assertIn("MIX_MODE_UNCONSUMED", rules)

    def test_two_vector_sets_without_wait(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 3),
            self._row("SET_INTRA_BLOCK", 11, 2, 3, core_type_id=3),
        ]
        self.assertEqual(self._rules(self._run_db(rows)).count("MIX_MODE_UNCONSUMED"), 2)

    def test_id_out_of_bounds_for_vector(self):
        rows = [self._row("SET_INTRA_BLOCK", 10, 1, 20)]
        self.assertIn("MIX_MODE_ID_OOB_VEC", self._rules(self._run_db(rows)))

    def test_id_out_of_bounds_for_cube(self):
        rows = [self._row("SET_INTRA_BLOCK", 10, 1, 40, core_type_id=1)]
        self.assertIn("MIX_MODE_ID_OOB_CUBE", self._rules(self._run_db(rows)))

    def test_without_force_reports_per_core_wait(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 3),
            self._row("WAIT_INTRA_BLOCK", 12, 2, 3, core_type_id=1),
        ]
        self.assertIn("BLOCK_WAIT_PREMATURE", self._rules(self._run_db(rows, mix_mode_force=False)))

    def test_cube_set_vector_wait_balanced(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 7, core_type_id=1),
            self._row("WAIT_INTRA_BLOCK", 12, 2, 7),
        ]
        self.assertEqual(self._run_db(rows), [])

    def test_cube_set_vector1_wait_balanced(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 20, core_type_id=1),
            self._row("WAIT_INTRA_BLOCK", 12, 2, 4, core_type_id=3),
        ]
        self.assertEqual(self._run_db(rows), [])

    def test_cube_set_unconsumed(self):
        rows = [self._row("SET_INTRA_BLOCK", 10, 1, 7, core_type_id=1)]
        self.assertIn("MIX_MODE_UNCONSUMED", self._rules(self._run_db(rows)))

    def test_full_cycle_balanced(self):
        rows = [
            self._row("SET_INTRA_BLOCK", 10, 1, 3),
            self._row("SET_INTRA_BLOCK", 11, 2, 3, core_type_id=3),
            self._row("WAIT_INTRA_BLOCK", 12, 3, 3, core_type_id=1),
            self._row("WAIT_INTRA_BLOCK", 13, 4, 19, core_type_id=1),
            self._row("SET_INTRA_BLOCK", 22, 5, 7, core_type_id=1),
            self._row("SET_INTRA_BLOCK", 23, 6, 20, core_type_id=1),
            self._row("WAIT_INTRA_BLOCK", 30, 7, 7),
            self._row("WAIT_INTRA_BLOCK", 31, 8, 4, core_type_id=3),
        ]
        self.assertEqual(self._run_db(rows), [])


class SyncCheckRedundancyTests(unittest.TestCase):
    """Redundant synchronization detection from the full instruction stream."""

    @staticmethod
    def _row(op, tick, instr_id, params):
        return RecordRow(
            instr_id,
            0,
            2,
            op,
            tick,
            0,
            0x9000D0D0E4 + instr_id,
            params,
        )

    def _run_db(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record_db(root, rows)
            return run(str(root))

    def _rules(self, hits):
        return [hit.rule for hit in hits]

    def _sync_params(self, target="VEC", consumer="MTE2", flag_id=0):
        return {
            "sync_kind": "SET_FLAG",
            "target_pipe": target,
            "consumer_pipe": consumer,
            "flag_id": flag_id,
        }

    def test_identical_set_flag_pair_is_redundant(self):
        rows = [
            self._row("SET_FLAG", 100, 1, self._sync_params()),
            self._row("SET_FLAG", 101, 2, self._sync_params()),
        ]
        self.assertEqual(self._rules(self._run_db(rows)).count("REDUNDANT_SET_FLAG"), 2)

    def test_identical_wait_flag_pair_is_redundant(self):
        params = self._sync_params()
        rows = [
            self._row("WAIT_FLAG", 100, 1, params),
            self._row("WAIT_FLAG", 101, 2, params),
        ]
        self.assertEqual(self._rules(self._run_db(rows)).count("REDUNDANT_WAIT_FLAG"), 2)

    def test_non_sync_instruction_breaks_redundancy(self):
        rows = [
            self._row("SET_FLAG", 100, 1, self._sync_params()),
            self._row("VADD", 101, 2, {"target_pipe": "VEC", "instr_type": "VEC"}),
            self._row("SET_FLAG", 102, 3, self._sync_params()),
        ]
        self.assertNotIn("REDUNDANT_SET_FLAG", self._rules(self._run_db(rows)))

    def test_different_flag_id_not_redundant(self):
        rows = [
            self._row("SET_FLAG", 100, 1, self._sync_params(flag_id=0)),
            self._row("SET_FLAG", 101, 2, self._sync_params(flag_id=1)),
        ]
        self.assertNotIn("REDUNDANT_SET_FLAG", self._rules(self._run_db(rows)))


class SyncCheckOutputTests(unittest.TestCase):
    def _violation(self, rule="WAIT_NO_ARRIVE", op="WAIT_FLAG"):
        return Violation(
            rule=rule,
            kernel_id="core0.veccore0",
            core="core0.veccore0",
            pc=0x9000D0D064,
            instr_id=2,
            op=op,
            pipe="VEC",
            trig_pipe="SCALAR",
            flag_id=5,
            detail="no SET_FLAG",
            file="f.dump",
            line_no=3,
            raw="[info] raw line",
            cycle=42,
        )

    def test_format_text_no_violations(self):
        self.assertEqual(format_text([]), "[sync_instruction_detection] No violations found.\n")

    def test_format_text_with_violations(self):
        text = format_text([self._violation()])
        self.assertIn("[sync_instruction_detection] Found 1 violation(s)", text)
        self.assertIn("[WAIT_NO_ARRIVE]", text)
        self.assertIn("cycle=42", text)
        self.assertIn("pc=0x9000d0d064", text)
        self.assertIn("no SET_FLAG", text)

    def test_format_text_kernel_end_violation_no_raw(self):
        v = Violation(
            rule="UNCONSUMED_ARRIVE",
            kernel_id="core0.veccore0",
            core="",
            pc=0,
            instr_id=0,
            op="KERNEL_END",
            pipe="SCALAR",
            trig_pipe="VEC",
            flag_id=5,
            detail="unconsumed=1",
            file="f.dump",
            line_no=0,
            raw="",
        )
        text = format_text([v])
        self.assertIn("kernel-end check", text)

    def test_format_json_structure(self):
        data = json.loads(format_json([self._violation()]))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["rule"], "WAIT_NO_ARRIVE")
        self.assertEqual(data[0]["kernel_id"], "core0.veccore0")
        self.assertEqual(data[0]["flag_id"], 5)
