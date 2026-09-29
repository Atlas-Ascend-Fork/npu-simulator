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

import tempfile
import unittest
from pathlib import Path

from npusim.core.record import artifacts


class ArchiveHelperDbsTests(unittest.TestCase):
    """archive_helper_dbs moves chip*.db from camodel cwd to record_dir."""

    def test_moves_chip_dbs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            camodel_path = root / "camodel"
            camodel_path.mkdir()
            record_dir = root / "record"
            record_dir.mkdir()

            (camodel_path / "chip0.db").write_text("db0")
            (camodel_path / "chip1.db").write_text("db1")
            (camodel_path / "other.txt").write_text("noise")

            ok = artifacts.archive_helper_dbs(record_dir, [camodel_path])

            self.assertTrue(ok)
            self.assertTrue((record_dir / "chip0.db").exists())
            self.assertTrue((record_dir / "chip1.db").exists())
            self.assertFalse((camodel_path / "chip0.db").exists())
            self.assertFalse((camodel_path / "chip1.db").exists())
            self.assertTrue((camodel_path / "other.txt").exists())

    def test_skips_root_equal_to_target(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            record_dir = root / "record"
            record_dir.mkdir()

            (record_dir / "chip0.db").write_text("already_here")

            ok = artifacts.archive_helper_dbs(record_dir, [record_dir])

            self.assertTrue(ok)
            self.assertTrue((record_dir / "chip0.db").exists())


class ArtifactsArchiveTests(unittest.TestCase):
    def test_archive_instr_bin_glob_moves_files(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            src = root / "src"
            src.mkdir()
            (src / "core0_instr.bin").write_bytes(b"bin")
            ok = artifacts.archive_instr_bin_glob(target, [src])
            self.assertTrue(ok)
            self.assertTrue((target / "core0_instr.bin").exists())
            self.assertFalse((src / "core0_instr.bin").exists())

    def test_archive_assets_moves_file(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            record_dir = root / "record"
            record_dir.mkdir()
            src = root / "src"
            src.mkdir()
            (src / "test.log").write_text("data")
            ok = artifacts.archive_assets(record_dir, ["test.log"], [src])
            self.assertTrue(ok)
            self.assertTrue((record_dir / "test.log").exists())

    def test_archive_assets_glob_replaces_targets_and_skips_duplicate_roots(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            src = root / "src"
            target = root / "target"
            src.mkdir()
            target.mkdir()
            (src / "stars_log0_task.dump").write_text("new file")
            (src / "stars_log1_task.dump").mkdir()
            (src / "stars_log1_task.dump" / "payload").write_text("new directory")
            (src / "keep.txt").write_text("untouched")
            (target / "stars_log0_task.dump").write_text("old file")
            (target / "stars_log1_task.dump").mkdir()
            (target / "stars_log1_task.dump" / "stale").write_text("old directory")

            ok = artifacts.archive_assets(target, ["stars_log*_task.dump"], [None, target, src, src])

            self.assertTrue(ok)
            self.assertEqual((target / "stars_log0_task.dump").read_text(), "new file")
            self.assertEqual((target / "stars_log1_task.dump" / "payload").read_text(), "new directory")
            self.assertFalse((target / "stars_log1_task.dump" / "stale").exists())
            self.assertFalse((src / "stars_log0_task.dump").exists())
            self.assertFalse((src / "stars_log1_task.dump").exists())
            self.assertEqual((src / "keep.txt").read_text(), "untouched")

    def test_archive_assets_cleans_residuals_when_target_unavailable(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            src = Path(tmp)
            (src / "residual.log").write_text("log")
            (src / "residual_dir").mkdir()
            (src / "residual_dir" / "payload").write_text("data")
            (src / "keep.txt").write_text("untouched")

            ok = artifacts.archive_assets(None, ["residual.log", "residual_dir"], [src], on_fail_delete=True)

            self.assertTrue(ok)
            self.assertFalse((src / "residual.log").exists())
            self.assertFalse((src / "residual_dir").exists())
            self.assertEqual((src / "keep.txt").read_text(), "untouched")

    def test_cleanup_internal_dirs_removes_logs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            record_dir = root / "record"
            (record_dir / "log").mkdir(parents=True)
            (record_dir / "log_ca").mkdir()
            artifacts.cleanup_internal_dirs(record_dir)
            self.assertFalse((record_dir / "log").exists())
            self.assertFalse((record_dir / "log_ca").exists())


if __name__ == "__main__":
    unittest.main()
