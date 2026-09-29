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

from npusim.core.report.report_path import _reorganize_reports_by_core

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


if __name__ == "__main__":
    unittest.main()
