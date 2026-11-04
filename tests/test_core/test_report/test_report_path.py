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
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.report.report_path import _find_chip_db_dirs, _reorganize_reports_by_core

report_mod = importlib.import_module("npusim.core.report.report")
version_mod = importlib.import_module("npusim.version")


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


class ReportPathTests(unittest.TestCase):
    def test_biprof_success_reorganizes_kernel_reports(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            kernel_dir = output_dir / "kernel_0_reports"
            export_dir.mkdir()
            kernel_dir.mkdir(parents=True)
            _write_helper_db(export_dir)
            trace_report = kernel_dir / "core_0_tracing_report_0.json"
            trace_report.write_text("{}", encoding="utf-8")
            smart_pipe_report = kernel_dir / "core_0_smart_pipe_report_0.json"
            smart_pipe_report.write_text("{}", encoding="utf-8")

            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                )

            self.assertTrue(ok)
            self.assertFalse(trace_report.exists())
            self.assertFalse(smart_pipe_report.exists())
            self.assertTrue((kernel_dir / "core_0" / "trace_core0.json").exists())
            self.assertTrue((kernel_dir / "core_0" / "trace_core0_smart_pipe.json").exists())

    def test_reorganize_moves_qi_core_reports_into_core_dir(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            kernel_dir = root / "results" / "kernel_0_reports"
            core_dir = kernel_dir / "core_0"
            core_dir.mkdir(parents=True)
            qi_report = kernel_dir / "qi_core_0_icache_utilization_report.html"
            qi_report.write_text("qi", encoding="utf-8")

            _reorganize_reports_by_core(root)

            self.assertFalse(qi_report.exists())
            self.assertTrue((core_dir / "qi_core_0_icache_utilization_report.html").exists())

    def test_reorganize_never_moves_npu_rep_archives(self):
        """Stage 3 writes {stem}.npu-rep into *_reports/ before reorganize
        runs; even a stem matching the core-file pattern must stay put."""
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            kernel_dir = root / "results" / "kernel_0_reports"
            kernel_dir.mkdir(parents=True)
            archive = kernel_dir / "core_2_add.npu-rep"
            archive.write_bytes(b"npu-rep\0")
            pending = kernel_dir / "core_2_icache_report.html"
            pending.write_text("x", encoding="utf-8")

            _reorganize_reports_by_core(root)

            self.assertTrue(archive.is_file())
            self.assertEqual(archive.read_bytes(), b"npu-rep\0")
            # The real per-core file next to it is still moved.
            self.assertFalse(pending.exists())
            self.assertTrue((kernel_dir / "core_2" / "core_2_icache_report.html").exists())


class FindChipDbDirsTests(unittest.TestCase):
    """Multi-chip per-chip DB dir detection (helper >= 0.8 layout)."""

    @staticmethod
    def _make_layout(root: Path):
        record_dir = root / "run" / "record"
        for chip in (0, 1):
            chip_dir = record_dir / f"chip{chip}"
            chip_dir.mkdir(parents=True)
            (chip_dir / "0000_kernel.db").write_bytes(b"")
        return record_dir

    def test_finds_chip_dirs_under_run_dir_and_record_dir(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            self._make_layout(root)

            from_run = _find_chip_db_dirs(root / "run")
            from_record = _find_chip_db_dirs(root / "run" / "record")

            self.assertEqual([p.name for p in from_run], ["chip0", "chip1"])
            self.assertEqual([p.name for p in from_record], ["chip0", "chip1"])
            self.assertEqual(from_run, from_record)

    def test_numeric_ordering(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            for chip in (10, 2):
                chip_dir = root / f"chip{chip}"
                chip_dir.mkdir()
                (chip_dir / "k.db").write_bytes(b"")
            self.assertEqual([p.name for p in _find_chip_db_dirs(root)], ["chip2", "chip10"])

    def test_ignores_flat_files_empty_dirs_and_non_chip_names(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            # helper 0.7.x 平铺 chip*.db 文件：不匹配（文件非目录）
            (root / "chip0.db").write_bytes(b"")
            (root / "chip0_instr.bin").write_bytes(b"")
            # 无 .db 的 chip 目录不匹配
            (root / "chip1").mkdir()
            # 非法 chip 命名不匹配
            results = root / "results"
            results.mkdir()
            (results / "x.db").write_bytes(b"")
            backups = root / "chip_backup"
            backups.mkdir()
            (backups / "x.db").write_bytes(b"")
            # 合法 chip 目录
            chip2 = root / "chip2"
            chip2.mkdir()
            (chip2 / "x.db").write_bytes(b"")

            found = _find_chip_db_dirs(root)

            self.assertEqual([p.name for p in found], ["chip2"])

    def test_empty_export(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            self.assertEqual(_find_chip_db_dirs(Path(tmp) / "nonexistent"), [])


if __name__ == "__main__":
    unittest.main()
