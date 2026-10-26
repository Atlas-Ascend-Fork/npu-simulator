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

"""Tests for stage 3 of report generation: per-DB CSV export + .npu-rep packing."""

import gzip
import importlib
import sqlite3
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

npu_rep_export_mod = importlib.import_module("npusim.core.report.npu_rep_export")
report_mod = importlib.import_module("npusim.core.report.report")
version_mod = importlib.import_module("npusim.version")

_HEADER_FORMAT = "<8sIHHIIIQ"
_ENTRY_FORMAT = "<8s128sHHIQQ"


def _write_contract_db(
    db_path: Path,
    kernel_name: str = "kernel",
    launch_id: str = "0",
) -> Path:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE KernelInfo(
                KernelInfoAttr VARCHAR(60) NOT NULL,
                KernelInfoVal VARCHAR(60) NOT NULL
            )
        """)
        conn.execute("INSERT INTO KernelInfo VALUES ('helper_version', ?)", (version_mod.VERSION,))
        conn.execute("INSERT INTO KernelInfo VALUES ('contract_version', '7')")
        conn.execute("INSERT INTO KernelInfo VALUES ('name', ?)", (kernel_name,))
        conn.execute("INSERT INTO KernelInfo VALUES ('launch_id', ?)", (launch_id,))
        conn.execute("CREATE TABLE Counter(Metric TEXT, Value INTEGER)")
        conn.execute("INSERT INTO Counter VALUES ('cycles', 42)")
    return db_path


def _decode_rep_entries(rep_path: Path) -> dict:
    """Minimal .npu-rep decoder: entry name -> payload bytes."""
    data = rep_path.read_bytes()
    magic, _version, _origin, head_len, count, _info_len, _reserved, _total = struct.unpack_from(
        _HEADER_FORMAT, data, 0
    )
    assert magic == b"npu-rep\0"
    assert head_len == struct.calcsize(_HEADER_FORMAT)
    entries = {}
    offset = head_len
    for _ in range(count):
        e_magic, name_raw, _ftype, _r1, _r2, length, rep_offset = struct.unpack_from(_ENTRY_FORMAT, data, offset)
        assert e_magic == b"npu-rep\0"
        name = name_raw.rstrip(b"\0").decode("utf-8")
        entries[name] = data[rep_offset : rep_offset + length]
        offset += struct.calcsize(_ENTRY_FORMAT)
    return entries


class ExportNpuRepTests(unittest.TestCase):
    def test_pack_single_db_with_reports(self):
        """Raw engine layout is packed with engine-original file names.

        Stage 3 runs before _reorganize_reports_by_core, so the archive must
        keep the engine names (core_{n}_tracing_report_{i}.json), NOT the
        dashboard rename (trace_core{n}.json) — .npu-rep consumers key on the
        original names.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            db_path = _write_contract_db(export_dir / "0000_kernel.db")
            results = output_dir / "results" / "0000_kernel_reports"
            results.mkdir(parents=True)
            (results / "summary.json").write_text('{"summary": true}')
            (results / "aicore_utilization.json").write_text('{"util": true}')
            (results / "core_0_tracing_report_0.json").write_text('{"trace": 0}')
            with gzip.open(results / "core_0_smart_pipe_report_0.json.gz", "wt") as gz:
                gz.write('{"smart_pipe": true}')

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            rep_path = results / "0000_kernel.npu-rep"
            self.assertTrue(rep_path.is_file())
            # The archive lives inside the kernel's reports dir, not at the output root.
            self.assertFalse((output_dir / "0000_kernel.npu-rep").exists())
            entries = _decode_rep_entries(rep_path)
            self.assertIn("KernelInfo.csv", entries)
            self.assertIn("Counter.csv", entries)
            self.assertIn("manifest.json", entries)
            self.assertIn("summary.json", entries)
            self.assertIn("aicore_utilization.json", entries)
            self.assertIn("core_0_tracing_report_0.json", entries)
            self.assertEqual(entries["core_0_tracing_report_0.json"], b'{"trace": 0}')
            self.assertIn("core_0_smart_pipe_report_0.json", entries)
            self.assertEqual(entries["core_0_smart_pipe_report_0.json"], b'{"smart_pipe": true}')
            # Compatibility contract: dashboard renames must NOT leak into the archive.
            self.assertNotIn("trace_core0.json", entries)
            self.assertNotIn("trace_core0_smart_pipe.json", entries)
            self.assertIn(b"cycles", entries["Counter.csv"])

    def test_copy_is_recursive_as_generality(self):
        """Nested JSONs (e.g. a pre-reorganized layout) are still copied flat."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            db_path = _write_contract_db(root / "0000_kernel.db")
            results = output_dir / "results" / "0000_kernel_reports"
            (results / "core_0").mkdir(parents=True)
            (results / "core_0" / "extra.json").write_text('{"extra": 1}')

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            entries = _decode_rep_entries(results / "0000_kernel.npu-rep")
            self.assertIn("extra.json", entries)

    def test_reports_dir_mapped_via_kernel_info_naming(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            db_path = _write_contract_db(root / "chip0.db", kernel_name="My Kernel/1", launch_id="7")
            results = output_dir / "results" / "0007_My_Kernel_1_reports"
            results.mkdir(parents=True)
            (results / "summary.json").write_text("{}")

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            entries = _decode_rep_entries(results / "chip0.npu-rep")
            self.assertIn("summary.json", entries)

    def test_prof_demo_style_reports_dir_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            db_path = _write_contract_db(root / "0000_kernel.db", kernel_name="ignored")
            results = output_dir / "results" / "kernel_0000_kernel_reports"
            results.mkdir(parents=True)
            (results / "summary.json").write_text("{}")

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            entries = _decode_rep_entries(results / "0000_kernel.npu-rep")
            self.assertIn("summary.json", entries)

    def test_multiple_dbs_each_get_own_rep_with_own_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            _write_contract_db(root / "0000_a.db", kernel_name="a", launch_id="0")
            _write_contract_db(root / "0001_b.db", kernel_name="b", launch_id="1")
            report_dirs = {}
            for stem, reports_dir_name in (("0000_a", "0000_a_reports"), ("0001_b", "0001_b_reports")):
                results = output_dir / "results" / reports_dir_name
                results.mkdir(parents=True)
                (results / f"{stem}.json").write_text("{}")
                report_dirs[stem] = results

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, root)

            entries_a = _decode_rep_entries(report_dirs["0000_a"] / "0000_a.npu-rep")
            entries_b = _decode_rep_entries(report_dirs["0001_b"] / "0001_b.npu-rep")
            self.assertIn("0000_a.json", entries_a)
            self.assertNotIn("0001_b.json", entries_a)
            self.assertIn("0001_b.json", entries_b)
            self.assertNotIn("0000_a.json", entries_b)
            # Nothing at the output root in the normal (reports-dir-found) case.
            self.assertFalse((output_dir / "0000_a.npu-rep").exists())
            self.assertFalse((output_dir / "0001_b.npu-rep").exists())

    def test_missing_reports_dir_packs_csv_only_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            db_path = _write_contract_db(root / "0000_kernel.db")

            import io

            captured = io.StringIO()
            with redirect_stdout(captured):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            entries = _decode_rep_entries(output_dir / "0000_kernel.npu-rep")
            self.assertIn("KernelInfo.csv", entries)
            self.assertIn("manifest.json", entries)
            self.assertNotIn("summary.json", entries)
            self.assertIn("No reports directory found", captured.getvalue())

    def test_staging_dir_is_transient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            output_dir.mkdir()
            (output_dir / "npu-rep").mkdir()
            (output_dir / "npu-rep" / "stale.txt").write_text("stale")
            db_path = _write_contract_db(root / "0000_kernel.db")

            with redirect_stdout(None):
                npu_rep_export_mod.export_npu_rep(output_dir, db_path)

            self.assertFalse((output_dir / "npu-rep").exists())
            self.assertTrue((output_dir / "0000_kernel.npu-rep").is_file())

    def test_best_effort_continues_past_corrupt_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            (root / "0000_bad.db").write_bytes(b"this is not a sqlite database")
            _write_contract_db(root / "0001_good.db")

            import io

            captured = io.StringIO()
            with redirect_stdout(captured):
                npu_rep_export_mod.export_npu_rep(output_dir, root)

            self.assertTrue((output_dir / "0001_good.npu-rep").is_file())
            self.assertFalse((output_dir / "0000_bad.npu-rep").exists())
            self.assertIn("npu-rep export failed for 0000_bad.db", captured.getvalue())
            self.assertIn("1/2", captured.getvalue())

    def test_no_db_input_warns_and_does_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "report"
            output_dir.mkdir()

            import io

            captured = io.StringIO()
            with redirect_stdout(captured):
                npu_rep_export_mod.export_npu_rep(output_dir, root)

            self.assertFalse(list(output_dir.iterdir()))
            self.assertIn("No contract DB found", captured.getvalue())


class ReportHookTests(unittest.TestCase):
    """The report.py hook drives stage 3 on the BiProf backend path."""

    class _BiProfSuccessChain:
        def execute(self, biprof_args, export_abs, output_abs, core_ids):
            return ("BiProfRunner module", True)

    class _ProfDemoSuccessChain:
        def execute(self, biprof_args, export_abs, output_abs, core_ids):
            return ("prof_demo", True)

    class _EngineSimulatingChain:
        """Mimics the engine: writes a raw (pre-reorganize) reports layout."""

        def execute(self, biprof_args, export_abs, output_abs, core_ids):
            reports = Path(output_abs) / "results" / "0000_kernel_reports"
            reports.mkdir(parents=True, exist_ok=True)
            (reports / "summary.json").write_text("{}")
            (reports / "core_0_tracing_report_0.json").write_text('{"trace": 0}')
            return ("BiProfRunner module", True)

    def test_generate_report_packs_npu_rep_for_biprof_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_contract_db(export_dir / "0000_kernel.db")

            with patch.object(report_mod, "_REPORT_CHAIN", self._BiProfSuccessChain()):
                ok = report_mod.generate_report(export=str(export_dir), output=str(output_dir), core_id="0")

            self.assertTrue(ok)
            self.assertTrue((output_dir / "0000_kernel.npu-rep").is_file())

    def test_generate_report_packs_engine_names_and_reorganizes_after(self):
        """Pins the ordering contract: stage 3 packs the raw engine output
        (engine-original trace names in the archive, archive placed inside the
        kernel's reports dir), then reorganize produces the dashboard layout
        on disk and leaves the archive untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_contract_db(export_dir / "0000_kernel.db")

            with patch.object(report_mod, "_REPORT_CHAIN", self._EngineSimulatingChain()):
                ok = report_mod.generate_report(export=str(export_dir), output=str(output_dir), core_id="0")

            self.assertTrue(ok)
            reports_dir = output_dir / "results" / "0000_kernel_reports"
            rep_path = reports_dir / "0000_kernel.npu-rep"
            self.assertTrue(rep_path.is_file())
            self.assertFalse((output_dir / "0000_kernel.npu-rep").exists())
            entries = _decode_rep_entries(rep_path)
            self.assertIn("core_0_tracing_report_0.json", entries)
            self.assertNotIn("trace_core0.json", entries)
            # On-disk dashboard layout: the real reorganize ran afterwards and
            # did not relocate the archive.
            self.assertTrue((reports_dir / "core_0" / "trace_core0.json").is_file())
            self.assertFalse((reports_dir / "core_0_tracing_report_0.json").exists())
            self.assertFalse((reports_dir / "core_0" / "0000_kernel.npu-rep").exists())

    def test_generate_report_skips_npu_rep_for_prof_demo_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            _write_contract_db(export_dir / "0000_kernel.db")

            with patch.object(report_mod, "_REPORT_CHAIN", self._ProfDemoSuccessChain()):
                ok = report_mod.generate_report(export=str(export_dir), output=str(output_dir), core_id="0")

            self.assertTrue(ok)
            self.assertFalse((output_dir / "results" / "0000_kernel_reports" / "0000_kernel.npu-rep").exists())


if __name__ == "__main__":
    unittest.main()
