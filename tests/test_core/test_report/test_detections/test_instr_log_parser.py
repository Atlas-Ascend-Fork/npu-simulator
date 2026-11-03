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

"""Unit tests for npusim.core.report.detections.instr_log_parser."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from npusim.core.report.detections.instr_log_parser import (
    RecordInstruction,
    _core_type_to_core_name,
    _core_type_to_ids,
    _core_type_to_rw_ids,
    _record_sync_semantics,
    _sync_op_order,
    collect_record_instructions,
    collect_record_sync_events,
    has_record_db_table_with_column,
)


def _write_db(dirpath, rows, with_size=True):
    """Create a record DB at dirpath/record.db with the given instruction rows.

    Row shape: (instr_id, core_id, core_type_id, op, tick, instr_type_id, pc, params-dict).
    """
    path = Path(dirpath) / "record.db"
    size_col = ", Size INTEGER" if with_size else ""
    with sqlite3.connect(path) as con:
        con.executescript(f"""
            CREATE TABLE ExecutedInstructions(
                ExecInstrId INTEGER PRIMARY KEY,
                ExecInstrCoreId INTEGER,
                CoreTypeId INTEGER,
                ExecInstrName TEXT,
                ExecInstrTickStart INTEGER,
                InstrTypeId INTEGER,
                SourceInstrAddr INTEGER,
                ExtendParams TEXT);
            CREATE TABLE KernelInfo(
                KernelInfoAttr VARCHAR(60) NOT NULL,
                KernelInfoVal VARCHAR(60) NOT NULL);
            CREATE TABLE MemoryRWAccesses(
                AccessedAddress INTEGER NOT NULL,
                AccessMode VARCHAR(1) NOT NULL,
                AccessTime INTEGER NOT NULL,
                MemoryType INTEGER NOT NULL,
                ExecInstrId INTEGER,
                CoreId INTEGER NOT NULL{size_col});
        """)
        con.executemany(
            "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (iid, core, ctype, op, tick, itype, pc, json.dumps(params) if params is not None else None)
                for iid, core, ctype, op, tick, itype, pc, params in rows
            ],
        )
    return path


class CoreTypeMappingTests(unittest.TestCase):
    def test_core_type_to_ids_sync_space(self):
        # sync space: veccore0 -> 1, veccore1 -> 2, cubecore -> 0
        self.assertEqual(_core_type_to_ids(3, 1), (3, 0))  # cube
        self.assertEqual(_core_type_to_ids(3, 2), (3, 1))  # vector0
        self.assertEqual(_core_type_to_ids(3, 3), (3, 2))  # vector1
        self.assertEqual(_core_type_to_ids(3, 0), (3, 0))  # unknown -> cube

    def test_core_type_to_rw_ids_rw_space(self):
        # rw space: veccore1 -> 1, everything else -> 0
        self.assertEqual(_core_type_to_rw_ids(3, 1), (3, 0))
        self.assertEqual(_core_type_to_rw_ids(3, 2), (3, 0))
        self.assertEqual(_core_type_to_rw_ids(3, 3), (3, 1))
        self.assertEqual(_core_type_to_rw_ids(3, 0), (3, 0))

    def test_core_type_to_core_name(self):
        self.assertEqual(_core_type_to_core_name(2, 1), "core2.cube0")
        self.assertEqual(_core_type_to_core_name(2, 2), "core2.vector0")
        self.assertEqual(_core_type_to_core_name(2, 3), "core2.vector1")


class SyncOpOrderTests(unittest.TestCase):
    def test_set_family_sorts_before_wait_family(self):
        self.assertEqual(_sync_op_order("SET_FLAG"), 0)
        self.assertEqual(_sync_op_order("SET_FLAGI"), 0)
        self.assertEqual(_sync_op_order("HSET_FLAG"), 0)
        self.assertEqual(_sync_op_order("SET_CROSS_CORE"), 0)
        self.assertEqual(_sync_op_order("SET_INTRA_BLOCK"), 0)
        self.assertEqual(_sync_op_order("SET_INTRA_BLOCKI"), 0)
        self.assertLess(_sync_op_order("SET_FLAG"), _sync_op_order("WAIT_FLAG"))

    def test_wait_family_then_other_ops(self):
        self.assertEqual(_sync_op_order("WAIT_FLAG"), 1)
        self.assertEqual(_sync_op_order("WAIT_FLAG_DEV"), 1)
        self.assertEqual(_sync_op_order("WAIT_INTRA_BLOCKI"), 1)
        self.assertEqual(_sync_op_order("GET_BUF"), 2)
        self.assertEqual(_sync_op_order("MOV_SRC_TO_DST_ALIGNv2"), 2)
        self.assertLess(_sync_op_order("WAIT_FLAG"), _sync_op_order("BAR"))


class RecordSyncSemanticsTests(unittest.TestCase):
    def test_extracts_flag_id_and_pipes(self):
        raw = json.dumps({"flag_id": 3, "target_pipe": "vec", "consumer_pipe": "mte2"})
        self.assertEqual(_record_sync_semantics(raw), (3, "VEC", "MTE2"))

    def test_sync_id_fallback_precedes_ffts(self):
        raw = json.dumps({"sync_id": 1, "ffts_flag_id": 9})
        self.assertEqual(_record_sync_semantics(raw)[0], 1)
        self.assertEqual(_record_sync_semantics(json.dumps({"ffts_flag_id": 9}))[0], 9)

    def test_missing_values_fall_back_to_conservative(self):
        self.assertEqual(_record_sync_semantics(None), (-1, "?", "?"))
        self.assertEqual(_record_sync_semantics("{}"), (-1, "?", "?"))
        # non-int flag id degrades to -1 instead of raising
        self.assertEqual(_record_sync_semantics(json.dumps({"flag_id": "x"})), (-1, "?", "?"))

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            _record_sync_semantics("not-json")
        # a JSON scalar is not an object either
        with self.assertRaises(ValueError):
            _record_sync_semantics("42")


class CollectRecordInstructionsTests(unittest.TestCase):
    def test_reads_rows_with_queue_type_and_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(
                tmp,
                [
                    (1, 0, 2, "STORE_2DV2", 100, 0, 0x10, {"queue_type": "simd"}),
                    (2, 0, 2, "SET_FLAG", 110, 0, 0x14, {"flag_id": 0, "target_pipe": "VEC", "consumer_pipe": "MTE2"}),
                ],
            )
            recs = collect_record_instructions(tmp)
            self.assertEqual([r.op for r in recs], ["STORE_2DV2", "SET_FLAG"])
            self.assertEqual(recs[0].queue_type, "SIMD")  # normalised to upper case
            self.assertTrue(recs[0].file.endswith("record.db"))

    def test_sort_key_is_tick_then_sync_op_order_then_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(
                tmp,
                [
                    (9, 0, 2, "WAIT_FLAG", 100, 0, 0x10, {"flag_id": 0, "target_pipe": "VEC", "consumer_pipe": "MTE2"}),
                    (2, 0, 2, "SET_FLAG", 100, 0, 0x14, {"flag_id": 0, "target_pipe": "VEC", "consumer_pipe": "MTE2"}),
                    (5, 0, 2, "NOP_PIPE", 100, 0, 0x18, {}),
                ],
            )
            recs = collect_record_instructions(tmp)
            self.assertEqual([r.op for r in recs], ["SET_FLAG", "WAIT_FLAG", "NOP_PIPE"])

    def test_ops_filter_and_sync_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(
                tmp,
                [
                    (1, 0, 2, "SET_FLAG", 100, 0, 0x10, {"flag_id": 0}),
                    (2, 0, 2, "STORE_2DV2", 110, 0, 0x14, {}),
                    (3, 0, 2, "WAIT_FLAG", 120, 0, 0x18, {"flag_id": 0}),
                ],
            )
            recs = collect_record_instructions(tmp, frozenset({"SET_FLAG", "WAIT_FLAG"}))
            self.assertEqual([r.op for r in recs], ["SET_FLAG", "WAIT_FLAG"])
            sync = collect_record_sync_events(tmp)
            self.assertEqual([r.op for r in sync], ["SET_FLAG", "WAIT_FLAG"])

    def test_invalid_extend_params_row_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "record.db"
            with sqlite3.connect(path) as con:
                con.executescript(
                    "CREATE TABLE ExecutedInstructions("
                    "ExecInstrId INTEGER PRIMARY KEY, ExecInstrCoreId INTEGER, CoreTypeId INTEGER, "
                    "ExecInstrName TEXT, ExecInstrTickStart INTEGER, InstrTypeId INTEGER, "
                    "SourceInstrAddr INTEGER, ExtendParams TEXT);"
                )
                con.executemany(
                    "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (1, 0, 2, "SET_FLAG", 100, 0, 0x10, "not-json"),
                        (2, 0, 2, "SET_FLAG", 110, 0, 0x14, json.dumps({"flag_id": 0})),
                    ],
                )
            recs = collect_record_instructions(tmp)
            self.assertEqual([r.instr_id for r in recs], [2])

    def test_start_pc_is_added_to_source_addr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_db(tmp, [(1, 0, 2, "NOP_PIPE", 100, 0, 0x10, {})])
            with sqlite3.connect(path) as con:
                con.execute("INSERT INTO KernelInfo VALUES ('start_pc', '4096')")
            recs = collect_record_instructions(tmp)
            self.assertEqual(recs[0].pc, 0x10 + 4096)

    def test_bar_is_sync_even_when_db_flags_is_sync_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(
                tmp,
                [
                    (1, 0, 1, "BAR", 100, 0, 0x10, {"is_sync": 0}),
                    (2, 0, 2, "STORE_2DV2", 110, 0, 0x14, {}),
                ],
            )
            sync = collect_record_sync_events(tmp)
            self.assertEqual([r.op for r in sync], ["BAR"])


class HasRecordDbTableWithColumnTests(unittest.TestCase):
    def test_true_when_column_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(tmp, [])
            self.assertTrue(has_record_db_table_with_column(Path(tmp), "MemoryRWAccesses", "Size"))

    def test_false_for_old_schema_without_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(tmp, [], with_size=False)
            self.assertFalse(has_record_db_table_with_column(Path(tmp), "MemoryRWAccesses", "Size"))
            # the table itself still exists, just without the probed column
            self.assertTrue(has_record_db_table_with_column(Path(tmp), "MemoryRWAccesses", "CoreId"))

    def test_false_for_missing_table_or_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_db(tmp, [])
            self.assertFalse(has_record_db_table_with_column(Path(tmp), "NoSuchTable", "Col"))
        self.assertFalse(has_record_db_table_with_column(Path("/nonexistent"), "T", "C"))

    def test_rejects_non_identifier_names(self):
        self.assertFalse(has_record_db_table_with_column(Path("."), "a; DROP TABLE x", "c"))
        self.assertFalse(has_record_db_table_with_column(Path("."), "t", "c--"))


class RecordInstructionDefaultsTests(unittest.TestCase):
    def test_queue_type_and_file_default_to_empty(self):
        rec = RecordInstruction(
            instr_id=1,
            pc=0,
            op="NOP_PIPE",
            tick=0,
            core_id=0,
            core_type_id=0,
            instr_type_id=0,
            is_sync=False,
            flag_id=-1,
            target_pipe="?",
            consumer_pipe="?",
        )
        self.assertEqual(rec.queue_type, "")
        self.assertEqual(rec.file, "")


if __name__ == "__main__":
    unittest.main()
