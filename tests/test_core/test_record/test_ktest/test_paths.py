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
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from npusim.core.record.ktest.paths import (
    KtestPathError,
    KtestPaths,
    resolve_ktest_paths,
)


def _fake_ascend_home(root: Path) -> Path:
    """Create a minimal ASCEND_HOME_PATH layout for ktest path resolution."""
    camodel = root / "tools/simulator/dav_3510/camodel"
    camodel.mkdir(parents=True)
    (camodel / "sim").write_text("", encoding="utf-8")
    return root


class KtestPathsTests(unittest.TestCase):
    def test_same_second_runs_keep_separate_record_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ascend_home = _fake_ascend_home(root / "toolkit")
            fixed_time = datetime(2026, 10, 27, 1, 0, 0, tzinfo=timezone.utc)
            paths_module = importlib.import_module("npusim.core.record.ktest.paths")
            with patch.dict(os.environ, {"ASCEND_HOME_PATH": str(ascend_home)}), patch.object(
                paths_module, "datetime"
            ) as clock:
                clock.now.return_value = fixed_time
                first, _ = resolve_ktest_paths(root / "out")
                (first.record_dir / "sentinel.txt").write_text("previous run", encoding="utf-8")
                second, context = resolve_ktest_paths(root / "out")

            self.assertNotEqual(first.target_dir, second.target_dir)
            self.assertEqual(context["OUTPUT PATH"], str(second.target_dir))
            self.assertFalse((second.record_dir / "sentinel.txt").exists())
            self.assertEqual((first.record_dir / "sentinel.txt").read_text(encoding="utf-8"), "previous run")

    def test_resolve_ktest_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            ascend_home = _fake_ascend_home(Path(tmp))
            output = Path(tmp) / "out"
            with patch.dict(os.environ, {"ASCEND_HOME_PATH": str(ascend_home)}):
                paths, ctx = resolve_ktest_paths(output, label="test_case")
            self.assertIsInstance(paths, KtestPaths)
            self.assertIn("ASCEND_HOME_PATH", ctx)
            self.assertIn("CAModel PATH", ctx)

    def test_resolve_ktest_paths_creates_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ascend_home = _fake_ascend_home(Path(tmp))
            output = Path(tmp) / "out"
            with patch.dict(os.environ, {"ASCEND_HOME_PATH": str(ascend_home)}):
                paths, _ = resolve_ktest_paths(output, label="case1")
            self.assertTrue(paths.target_dir.exists())
            self.assertTrue(paths.record_dir.exists())
            self.assertTrue(paths.record_dir.exists())

    def test_resolve_ktest_paths_missing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(KtestPathError):
                    resolve_ktest_paths(output, label="case1")

    def test_resolve_ktest_paths_missing_camodel(self):
        with tempfile.TemporaryDirectory() as tmp:
            ascend_home = Path(tmp)  # no camodel layout
            output = Path(tmp) / "out"
            with patch.dict(os.environ, {"ASCEND_HOME_PATH": str(ascend_home)}):
                with self.assertRaises(KtestPathError):
                    resolve_ktest_paths(output, label="case1")


if __name__ == "__main__":
    unittest.main()
