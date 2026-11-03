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

import importlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stderr
from unittest.mock import patch

report_mod = importlib.import_module("npusim.core.report.report")
version_mod = importlib.import_module("npusim.version")
strategy_impl_mod = importlib.import_module("npusim.core.report.report_strategy_impl")


def _write_helper_db(export_dir: Path) -> Path:
    record_dir = export_dir / "record"
    record_dir.mkdir(parents=True, exist_ok=True)
    db_path = record_dir / "0000_kernel.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE KernelInfo(
                KernelInfoAttr VARCHAR(60) NOT NULL,
                KernelInfoVal VARCHAR(60) NOT NULL
            )
        """)
        conn.execute(
            "INSERT INTO KernelInfo VALUES ('helper_version', ?)",
            (version_mod.VERSION,),
        )
        conn.execute("INSERT INTO KernelInfo VALUES ('contract_version', '7')")
    return db_path


class _BiProfSuccessChain:
    def execute(self, biprof_args, export_abs, output_abs, core_ids):
        return ("BiProfRunner module", True)


class _CaptureArgsChain:
    def __init__(self):
        self.biprof_args = None
        self.calls = []

    def execute(self, biprof_args, export_abs, output_abs, core_ids):
        self.biprof_args = list(biprof_args)
        self.calls.append(list(biprof_args))
        return ("BiProfRunner module", False)


FallbackChain = strategy_impl_mod.FallbackChain


class ReportTests(unittest.TestCase):
    def test_report_rejects_wrong_contract(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            db_path = _write_helper_db(export_dir)
            with sqlite3.connect(db_path) as conn:
                conn.execute("UPDATE KernelInfo SET KernelInfoVal='5' WHERE KernelInfoAttr='contract_version'")

            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(export=str(export_dir), output=str(output_dir), core_id="0")

            self.assertFalse(ok)

    def test_core_id_is_forwarded_to_biprof_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_helper_db(export_dir)
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="all",
                )

            self.assertFalse(ok)
            self.assertIn("--core-id", chain.biprof_args)
            idx = chain.biprof_args.index("--core-id")
            self.assertEqual(chain.biprof_args[idx + 1], "all")

    def test_single_card_defaults_include_ipc_not_smart_pipes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_helper_db(export_dir)
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            self.assertFalse(ok)
            self.assertIn("--scalar-ipc", chain.biprof_args)
            self.assertIn("--vec-ipc", chain.biprof_args)
            self.assertNotIn("--smart-pipes", chain.biprof_args)

    def test_single_card_smart_pipes_flag_is_forwarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_helper_db(export_dir)
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                    smart_pipes=True,
                )

            self.assertFalse(ok)
            self.assertIn("--smart-pipes", chain.biprof_args)

    def test_record_db_directory_is_the_only_report_input(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            record_dir = export_dir / "record"
            _write_helper_db(export_dir)
            (record_dir / "instr.bin").write_bytes(b"bin")
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            self.assertFalse(ok)
            self.assertIn("--input-db", chain.biprof_args)
            self.assertNotIn("--binary-log", chain.biprof_args)
            idx = chain.biprof_args.index("--input-db")
            self.assertEqual(chain.biprof_args[idx + 1], str(record_dir.resolve()))

    def test_report_dispatches_chip_instr_bins_to_multi_chip(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )
            for args in chain.calls:
                self.assertIn("--binary-log", args)
                self.assertNotIn("--input-db", args)

    def test_generate_report_falls_back_to_instr_bin(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            (export_dir / "instr.bin").write_bytes(b"bin")
            chain = _CaptureArgsChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            self.assertFalse(ok)
            self.assertIn("--binary-log", chain.biprof_args)
            self.assertNotIn("--input-db", chain.biprof_args)

    def test_detection_without_record_db_is_skipped_not_failed(self):
        """An instr.bin-only report still succeeds; sync detection reports the skip."""
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            (export_dir / "instr.bin").write_bytes(b"bin")
            chain = _BiProfSuccessChain()

            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            self.assertTrue(ok)
            report_file = output_dir / "sync_instruction_detection_report.txt"
            self.assertIn("Skipped: no cannsim_helper record DB", report_file.read_text(encoding="utf-8"))

    def test_data_race_detection_is_reachable_and_writes_report(self):
        """report.py must expose a data-race entry; it reads the helper record DB."""
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            db_path = _write_helper_db(export_dir)
            with sqlite3.connect(db_path) as con:
                con.execute(
                    "CREATE TABLE ExecutedInstructions("
                    "ExecInstrId INTEGER PRIMARY KEY, ExecInstrCoreId INTEGER, CoreTypeId INTEGER, "
                    "ExecInstrName TEXT, ExecInstrTickStart INTEGER, InstrTypeId INTEGER, "
                    "SourceInstrAddr INTEGER, ExtendParams TEXT)"
                )
                con.execute(
                    "CREATE TABLE MemoryRWAccesses("
                    "AccessedAddress INTEGER NOT NULL, AccessMode VARCHAR(1) NOT NULL, "
                    "AccessTime INTEGER NOT NULL, MemoryType INTEGER NOT NULL, ExecInstrId INTEGER, "
                    "CoreId INTEGER NOT NULL, Size INTEGER, Step INTEGER)"
                )
                # Two same-core writes to an overlapping GM address -> GM_WAW_OVERLAP
                con.executemany(
                    "INSERT INTO MemoryRWAccesses VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (0x1000, "W", 1, 0, 1, 0, 512, 1),
                        (0x1000, "W", 2, 0, 2, 0, 512, 2),
                    ],
                )

            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            # Violations are report results, not tool failures.
            self.assertTrue(ok)
            race_report = output_dir / "data_race_detection_report.txt"
            self.assertIn("GM_WAW_OVERLAP", race_report.read_text(encoding="utf-8"))
            self.assertTrue((output_dir / "sync_instruction_detection_report.txt").exists())


def _write_chip_helper_db(db_path, chip_id=0):
    """Write a minimal cannsim_helper DB at db_path with given chip_id."""

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS KernelInfo("
            "KernelInfoAttr VARCHAR(60) NOT NULL, "
            "KernelInfoVal VARCHAR(60) NOT NULL)"
        )
        conn.execute(
            "INSERT INTO KernelInfo VALUES ('helper_version', ?)",
            (version_mod.VERSION,),
        )
        conn.execute("INSERT INTO KernelInfo VALUES ('contract_version', '7')")
        conn.execute("INSERT INTO KernelInfo VALUES ('chip_id', ?)", (str(chip_id),))
        conn.execute("INSERT INTO KernelInfo VALUES ('cores_per_chip', '32')")


class MultiChipReportDbTests(unittest.TestCase):
    """report() with chip*_instr.bin + matching chip*.db uses --input-db per chip."""

    def test_multi_chip_with_helper_dbs_uses_input_db(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()

            # Create chip instr bins
            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")

            # Create matching helper DBs
            _write_chip_helper_db(export_dir / "chip0.db", chip_id=0)
            _write_chip_helper_db(export_dir / "chip1.db", chip_id=1)

            chain = _CaptureArgsChain()
            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="all",
                )

            self.assertEqual(len(chain.calls), 2)
            for args in chain.calls:
                self.assertIn("--input-db", args)

    def test_multi_chip_without_dbs_falls_back_to_instr_bin(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()

            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")

            chain = _CaptureArgsChain()
            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="all",
                )

            self.assertEqual(len(chain.calls), 2)
            for args in chain.calls:
                self.assertIn("--binary-log", args)
                self.assertNotIn("--input-db", args)

    def test_multi_chip_db_in_record_subdir(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            record_dir = export_dir / "record"
            output_dir = root / "report"
            export_dir.mkdir()
            record_dir.mkdir()

            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")

            _write_chip_helper_db(record_dir / "chip0.db", chip_id=0)
            _write_chip_helper_db(record_dir / "chip1.db", chip_id=1)

            chain = _CaptureArgsChain()
            with patch.object(report_mod, "_REPORT_CHAIN", chain):
                report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="all",
                )

            self.assertEqual(len(chain.calls), 2)
            for args in chain.calls:
                self.assertIn("--input-db", args)

    def test_backend_failure_surfaces_biprof_log_root_cause(self):
        # The real root cause ("Failed to find instruction logs") is written to
        # {output_abs}/results/runner.py.log and must be surfaced first, instead of
        # the misleading "Please install Python report dependencies" hint.
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results_dir = root / "results"
            results_dir.mkdir()
            (results_dir / "runner.py.log").write_text(
                "[INFO] Running ProfRunner module...\n"
                "[ERROR] biprof failed with the following error: "
                "Failed to find instruction logs. Please make sure the "
                "logs folder is not empty and contains the instr.bin file.\n",
                encoding="utf-8",
            )
            with redirect_stderr(err):
                backend, ok = FallbackChain([]).execute(
                    [],
                    export_abs=root,
                    output_abs=root,
                    core_ids=(0,),
                )

        self.assertEqual(backend, "")
        self.assertFalse(ok)
        text = err.getvalue()
        self.assertIn("Root cause:", text)
        self.assertIn("Failed to find instruction logs", text)
        self.assertIn("runner.py.log", text)
        # Once the root cause is located, do not fall back to the misleading hint.
        self.assertNotIn("Please install Python report dependencies", text)

    def test_backend_failure_empty_instr_bin_root_cause(self):
        # Another root cause: an empty instr.bin -> "Binary calog file is empty".
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results_dir = root / "results"
            results_dir.mkdir()
            (results_dir / "runner.py.log").write_text(
                "[ERROR] Binary calog file is empty: /tmp/x/instr.bin\n",
                encoding="utf-8",
            )
            with redirect_stderr(err):
                FallbackChain([]).execute(
                    [],
                    export_abs=root,
                    output_abs=root,
                    core_ids=(0,),
                )

        text = err.getvalue()
        self.assertIn("Binary calog file is empty", text)
        self.assertNotIn("Please install Python report dependencies", text)

    def test_backend_failure_falls_back_to_install_hint_without_log(self):
        # Without biprof logs (module not loaded / no backend attempted), fall back
        # to the generic hint, which still points to prof_demo.
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with redirect_stderr(err):
                FallbackChain([]).execute(
                    [],
                    export_abs=root,
                    output_abs=root,
                    core_ids=(0,),
                )

        text = err.getvalue()
        self.assertIn("reinstall the cannsim wheel", text)
        self.assertIn("prof_demo", text)


if __name__ == "__main__":
    unittest.main()
