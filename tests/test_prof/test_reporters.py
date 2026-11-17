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

"""Tests for the BiProf hotspot reporter's per-DB handling."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from npusim.core.public.aicore_objects import DeviceObject
from npusim.prof.src.backend import reporters
from npusim.prof.src.common.biprof_context import biprof_ctx

from npusim.prof.src.backend.reporters import HotspotsDbOnlyReporter


class EmptyKernelDbTests(unittest.TestCase):
    def test_skip_db_without_executed_instructions(self):
        """Warmup kernels produce DBs with zero instructions; report must skip them."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "0000_warmup.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE KernelInfo (KernelInfoAttr TEXT, KernelInfoVal TEXT)")
            conn.execute("CREATE TABLE ExecutedInstructions (ExecInstrId INTEGER)")
            conn.commit()
            conn.close()

            ret = HotspotsDbOnlyReporter()._process_one_db(str(db_path), 0)

            self.assertEqual(ret, 0)


class PerKernelObjectTests(unittest.TestCase):
    def test_context_restored_when_object_processor_fails(self):
        old = dict(biprof_ctx.__dict__)
        self.addCleanup(lambda: (biprof_ctx.__dict__.clear(), biprof_ctx.__dict__.update(old)))
        biprof_ctx.object_file = "explicit.o"
        biprof_ctx.kernel_name = "original"
        reporter = HotspotsDbOnlyReporter()
        reporter._object_resolver = MagicMock()
        reporter._object_resolver.select.return_value = DeviceObject("device.o", "raw_symbol")
        with patch.object(reporters, "database_handler") as db, patch.object(
            reporters, "BackendDBManager"
        ), patch.object(reporter, "_get_kernel_name", return_value="alpha"), patch.object(
            reporter, "_get_launch_id", return_value=0
        ), patch.object(reporters, "ObjectProcessor", side_effect=ValueError("bad object")):
            db.count_rows.return_value = 1
            with self.assertRaisesRegex(ValueError, "bad object"):
                reporter._process_one_db("alpha.db", 0)
            db.disconnect.assert_called_once()
        self.assertEqual(biprof_ctx.object_file, "explicit.o")
        self.assertEqual(biprof_ctx.kernel_name, "original")

    def test_object_processor_and_analyzers_receive_current_kernel(self):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            old = dict(biprof_ctx.__dict__)
            self.addCleanup(lambda: (biprof_ctx.__dict__.clear(), biprof_ctx.__dict__.update(old)))
            biprof_ctx.__dict__.pop("object_file", None)
            biprof_ctx.__dict__.pop("kernel_name", None)
            biprof_ctx.init(SimpleNamespace(object_file=None, kernel_name=None, save_tmp_files=True))
            reporter = HotspotsDbOnlyReporter()
            reporter._input_db = str(root)
            reporter._output_dir = str(root)
            reporter._arch = None
            reporter._analysis_name = "hotspots"
            reporter._input_dbs = [str(root / (name + ".db")) for name in ("alpha", "beta", "missing")]
            selected = [DeviceObject("a.o", "_Z5alphav"), DeviceObject("b.o", "_Z4betav"), None]
            resolver = stack.enter_context(patch.object(reporters, "DeviceObjectResolver")).return_value
            resolver.select.side_effect = selected
            db = stack.enter_context(patch.object(reporters, "database_handler"))
            db.table_exists.return_value = True
            db.count_rows.return_value = 1
            db.get_arch.return_value = None
            stack.enter_context(patch.object(reporter, "_get_kernel_name", side_effect=["alpha", "beta", "missing"]))
            stack.enter_context(patch.object(reporter, "_get_launch_id", side_effect=[0, 1, 2]))
            manager = stack.enter_context(patch.object(reporters, "BackendDBManager"))
            obj = stack.enter_context(patch.object(reporters, "ObjectProcessor"))
            obj.return_value.read_for_launch.return_value = (MagicMock(), [])
            processor = stack.enter_context(patch.object(reporters, "HotspotsProcessor"))
            for name in ("generate_chrome_tracing", "generate_arch_diagram", "generate_summary_report"):
                getattr(processor.return_value, name).return_value = 0
            observations = []

            def analyzer():
                observations.append((biprof_ctx.object_file, biprof_ctx.kernel_name))
                return MagicMock()

            stack.enter_context(patch.object(reporters, "AnalyzerManager", side_effect=analyzer))
            stack.enter_context(patch.object(reporters, "HintManager"))
            self.assertEqual(reporter.process(), 0)
            self.assertEqual(observations, [("a.o", "_Z5alphav"), ("b.o", "_Z4betav"), (None, "missing")])
            self.assertEqual([c.args[2] for c in obj.call_args_list], ["a.o", "b.o"])
            self.assertEqual(
                [c.args[0].kernel for c in obj.return_value.read_for_launch.call_args_list], ["_Z5alphav", "_Z4betav"]
            )
            self.assertEqual(manager.save_processed_data.call_count, 2)
            self.assertIsNone(biprof_ctx.object_file)
            self.assertIsNone(biprof_ctx.kernel_name)
            self.assertNotIn("object_file", biprof_ctx.__dict__)
            resolver.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
