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
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from npusim.core.record.ktest.config import KtestConfig
from npusim.core.record.ktest.ktest import CannsimKtest, ktest

# py3.10 的 mock 逐级 getattr 解析字符串目标，会被 __init__ 里
# from-import 的同名函数遮蔽（npusim.core.record / ktest），改用 patch.object。
_ktest_mod = importlib.import_module("npusim.core.record.ktest.ktest")


def _make_case_dir(tmp, chips=1, extra=None):
    case_dir = Path(tmp) / "case"
    case_dir.mkdir()
    data = {f"chip{i}": {"die0": {}} for i in range(chips)}
    (case_dir / "top.json").write_text(json.dumps(data))
    return case_dir


class CannsimKtestStage1Tests(unittest.TestCase):
    def test_stage1_missing_case_dir(self):
        cfg = KtestConfig(case_dir="/nonexistent")
        kt = CannsimKtest(cfg)
        self.assertFalse(kt.stage1_preflight())

    def test_stage1_not_a_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file.txt"
            f.write_text("x")
            kt = CannsimKtest(KtestConfig(case_dir=str(f)))
            self.assertFalse(kt.stage1_preflight())

    def test_stage1_no_top_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            kt = CannsimKtest(KtestConfig(case_dir=str(case_dir)))
            self.assertFalse(kt.stage1_preflight())

    def test_stage1_valid_1p(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = _make_case_dir(tmp, chips=1)
            kt = CannsimKtest(KtestConfig(case_dir=str(case_dir)))
            self.assertTrue(kt.stage1_preflight())
            self.assertEqual(kt.runtime_config, "1p")

    def test_stage1_valid_2p(self):
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = _make_case_dir(tmp, chips=2)
            kt = CannsimKtest(KtestConfig(case_dir=str(case_dir)))
            self.assertTrue(kt.stage1_preflight())
            self.assertEqual(kt.runtime_config, "2p")


class CannsimKtestStage2Tests(unittest.TestCase):
    def test_stage2_missing_env(self):
        cfg = KtestConfig(case_dir="/tmp/case")
        kt = CannsimKtest(cfg)
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(kt.stage2_resolve_paths())

    def test_stage2_lock_failure(self):
        cfg = KtestConfig(case_dir="/tmp/case")
        kt = CannsimKtest(cfg)
        with patch.object(_ktest_mod, "resolve_ktest_paths") as m_res, patch.object(
            _ktest_mod, "acquire_camodel_lock", return_value=None
        ):
            m_res.return_value = (MagicMock(), {"x": 1})
            self.assertFalse(kt.stage2_resolve_paths())

    def test_stage2_success(self):
        cfg = KtestConfig(case_dir="/tmp/case")
        kt = CannsimKtest(cfg)
        paths = MagicMock()
        paths.target_dir = Path("/tmp/out/target")
        paths.record_dir = Path("/tmp/out/target/record")
        with patch.object(_ktest_mod, "resolve_ktest_paths", return_value=(paths, {"x": 1})), patch.object(
            _ktest_mod, "acquire_camodel_lock", return_value=object()
        ):
            self.assertTrue(kt.stage2_resolve_paths())
            self.assertIsNotNone(kt.paths)


class CannsimKtestStage3Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = KtestConfig(case_dir="/tmp/case")
        self.kt = CannsimKtest(self.cfg)
        self.kt.paths = MagicMock()
        self.kt.paths.case_symlink = Path("/tmp/camodel/case")
        self.kt.paths.camodel_path = Path("/tmp/camodel")
        self.kt.paths.sim_path = Path("/tmp/camodel/sim")
        self.kt.paths.record_dir = Path("/tmp/out/record")
        self.kt.record_dir = Path("/tmp/out/record")
        self.kt.cannsim_log = Path("/tmp/out/npusim.log")
        self.kt.config_template = None

    def test_stage3_success(self):
        with patch.object(_ktest_mod, "create_case_symlink", return_value=True), patch.object(
            _ktest_mod, "prepare_runtime_dirs", return_value=True
        ), patch.object(_ktest_mod, "ensure_sim_executable", return_value=True), patch.object(
            _ktest_mod, "build_execution_env", return_value={}
        ), patch.object(_ktest_mod, "run_user_app_with_dashboard") as m_run, patch.object(
            _ktest_mod, "archive_instr_bin_glob"
        ), patch.object(_ktest_mod, "archive_helper_dbs"), patch.object(
            _ktest_mod, "remove_case_symlink"
        ), patch.object(_ktest_mod.shutil, "rmtree"):
            m_run.return_value = MagicMock(
                success=True,
                interrupted=False,
                stderr_tail="",
                dashboard=None,
                returncode=0,
            )
            self.kt.record_dir = MagicMock()
            self.kt.record_dir.__truediv__.return_value = MagicMock()
            ok = self.kt.stage3_execute()
            self.assertTrue(ok)

    def test_stage3_helper_not_found(self):
        from npusim.core.record.paths import RecordPathError

        with patch.object(_ktest_mod, "create_case_symlink", return_value=True), patch.object(
            _ktest_mod, "prepare_runtime_dirs", return_value=True
        ), patch.object(_ktest_mod, "ensure_sim_executable", return_value=True), patch.object(
            _ktest_mod, "build_execution_env", side_effect=RecordPathError("no helper")
        ), patch.object(_ktest_mod, "archive_instr_bin_glob"), patch.object(
            _ktest_mod, "archive_helper_dbs"
        ), patch.object(_ktest_mod, "remove_case_symlink"):
            self.kt.record_dir = MagicMock()
            self.kt.record_dir.__truediv__.return_value = MagicMock()
            ok = self.kt.stage3_execute()
            self.assertFalse(ok)

    def test_stage3_keyboard_interrupt(self):
        with patch.object(_ktest_mod, "create_case_symlink", return_value=True), patch.object(
            _ktest_mod, "prepare_runtime_dirs", return_value=True
        ), patch.object(_ktest_mod, "ensure_sim_executable", return_value=True), patch.object(
            _ktest_mod, "build_execution_env", return_value={}
        ), patch.object(_ktest_mod, "run_user_app_with_dashboard", side_effect=KeyboardInterrupt()), patch.object(
            _ktest_mod, "archive_instr_bin_glob"
        ), patch.object(_ktest_mod, "archive_helper_dbs"), patch.object(_ktest_mod, "remove_case_symlink"):
            self.kt.record_dir = MagicMock()
            self.kt.record_dir.__truediv__.return_value = MagicMock()
            ok = self.kt.stage3_execute()
            self.assertFalse(ok)
            self.assertTrue(self.kt._interrupted)


class KtestOrchestrationTests(unittest.TestCase):
    @patch.object(CannsimKtest, "stage1_preflight", return_value=True)
    @patch.object(CannsimKtest, "stage2_resolve_paths", return_value=True)
    @patch.object(CannsimKtest, "stage3_execute", return_value=True)
    def test_run_success(self, s1, s2, s3):
        kt = CannsimKtest(KtestConfig(case_dir="/tmp/case"))
        self.assertTrue(kt.run())

    @patch.object(CannsimKtest, "stage1_preflight", return_value=False)
    def test_run_stage1_fails(self, s1):
        kt = CannsimKtest(KtestConfig(case_dir="/tmp/case"))
        self.assertFalse(kt.run())

    @patch.object(_ktest_mod, "print_start_banner")
    @patch.object(_ktest_mod, "print_record_summary")
    @patch.object(_ktest_mod, "release_camodel_lock")
    def test_ktest_success_path(self, m_lock, m_sum, m_banner):
        cfg = KtestConfig(case_dir="/tmp/case")
        with patch.object(CannsimKtest, "run", return_value=True), patch.object(_ktest_mod, "generate_auto_report"):
            ok = ktest(cfg)
            self.assertTrue(ok)
            m_lock.assert_called_once_with(None)

    @patch.object(_ktest_mod, "print_start_banner")
    @patch.object(_ktest_mod, "print_record_summary")
    @patch.object(_ktest_mod, "release_camodel_lock")
    def test_ktest_with_gen_report(self, m_lock, m_sum, m_banner):
        cfg = KtestConfig(case_dir="/tmp/case", gen_report="vf")
        with patch.object(CannsimKtest, "run", return_value=True), patch.object(
            _ktest_mod, "generate_auto_report"
        ) as m_gr:
            executor = CannsimKtest(cfg)
            executor.paths = MagicMock()
            executor.paths.case_symlink = Path("/tmp/camodel/case")
            executor.record_dir = Path("/tmp/out/record")
            executor.target_dir = Path("/tmp/out/target")
            executor._lock_fp = None
            with patch.object(CannsimKtest, "run", return_value=True), patch.object(_ktest_mod, "print_record_summary"):
                ok = ktest(cfg)
            self.assertTrue(ok)
            m_gr.assert_called_once()


if __name__ == "__main__":
    unittest.main()
