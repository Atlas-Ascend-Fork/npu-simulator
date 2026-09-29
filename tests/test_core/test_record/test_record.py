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

"""record.py 全覆盖测试：_format_cores、_maybe_sleep、stage1/2/3、run、record()。"""

import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch, MagicMock

from npusim.core.record.record import CannsimRecord, record, _maybe_sleep
from npusim.core.record.config import RecordConfig

# py3.10 的 mock 逐级 getattr 解析字符串目标，会被 __init__ 里
# from-import 的同名函数遮蔽（npusim.core.record），改用 patch.object。
_record_mod = importlib.import_module("npusim.core.record.record")


# =============================================================================
# _format_cores（纯逻辑，边界全覆盖）
# =============================================================================
class FormatCoresTests(unittest.TestCase):
    def _fmt(self, cores):
        return CannsimRecord._format_cores(cores)

    def test_empty(self):
        self.assertEqual(self._fmt(()), "(none)")

    def test_single(self):
        self.assertEqual(self._fmt((0,)), "0")
        self.assertEqual(self._fmt((7,)), "7")

    def test_consecutive_range(self):
        self.assertEqual(self._fmt((0, 1, 2)), "0-2")

    def test_two_ranges(self):
        self.assertEqual(self._fmt((0, 1, 3, 4)), "0-1,3-4")

    def test_single_gap(self):
        self.assertEqual(self._fmt((0, 2, 3)), "0,2-3")

    def test_example_from_docstring(self):
        self.assertEqual(self._fmt((0, 1, 2, 5)), "0-2,5")

    def test_unsorted_input(self):
        self.assertEqual(self._fmt((5, 1, 3)), "1,3,5")


# =============================================================================
# _maybe_sleep
# =============================================================================
class MaybeSleepTests(unittest.TestCase):
    @patch.object(_record_mod, "sleep")
    def test_sleeps_by_default(self, mock_sleep):
        _maybe_sleep(0.5)
        mock_sleep.assert_called_once_with(0.5)

    @patch.object(_record_mod, "sleep")
    def test_skips_when_npusim_no_delay(self, mock_sleep):
        with patch.dict(os.environ, {"NPUSIM_NO_DELAY": "1"}):
            _maybe_sleep(0.5)
        mock_sleep.assert_not_called()

    @patch.object(_record_mod, "sleep")
    def test_skips_when_cannsim_no_delay(self, mock_sleep):
        with patch.dict(os.environ, {"CANNSIM_NO_DELAY": "1"}):
            _maybe_sleep(0.5)
        mock_sleep.assert_not_called()


# =============================================================================
# CannsimRecord.__init__ + stage1_parameter_check
# =============================================================================
class Stage1ParameterCheckTests(unittest.TestCase):
    def setUp(self):
        self.cfg = RecordConfig(
            user_app="python3 test_ops.py",
            output=Path("/tmp/out"),
            soc_version="Ascend950",
            user_option="",
        )

    def test_init_sets_fields(self):
        r = CannsimRecord(self.cfg)
        self.assertEqual(r.model, "CA")
        self.assertEqual(r.soc_version, "Ascend950")
        self.assertIsNotNone(r.launch)

    def test_stage1_command_launch_valid(self):
        r = CannsimRecord(
            RecordConfig(
                user_app="python3 train.py --lr=1e-3",
                output=Path("/tmp/out"),
                soc_version="Ascend950",
            )
        )
        self.assertTrue(r.stage1_parameter_check())

    def test_stage1_missing_user_app(self):
        cfg = RecordConfig(user_app="", output=Path("/tmp/out"), soc_version="Ascend950")
        r = CannsimRecord(cfg)
        self.assertFalse(r.stage1_parameter_check())

    def test_stage1_binary_file_not_found(self):
        cfg = RecordConfig(user_app="/nonexistent/bin", output=Path("/tmp/out"), soc_version="Ascend950")
        r = CannsimRecord(cfg)
        self.assertFalse(r.stage1_parameter_check())

    def test_stage1_unsupported_soc(self):
        cfg = RecordConfig(user_app="ls", output=Path("/tmp/out"), soc_version="UnknownSoc")
        r = CannsimRecord(cfg)
        self.assertFalse(r.stage1_parameter_check())

    def test_stage1_invalid_core_id(self):
        cfg = RecordConfig(
            user_app="python3 train.py",
            output=Path("/tmp/out"),
            soc_version="Ascend950",
            core_id="999",
        )
        r = CannsimRecord(cfg)
        self.assertFalse(r.stage1_parameter_check())

    def test_stage1_valid_core_id(self):
        cfg = RecordConfig(
            user_app="python3 train.py",
            output=Path("/tmp/out"),
            soc_version="Ascend950",
            core_id="0-2,5",
        )
        r = CannsimRecord(cfg)
        self.assertTrue(r.stage1_parameter_check())
        self.assertEqual(r.resolved_cores, (0, 1, 2, 5))

    def test_stage1_no_core_id_sets_none(self):
        r = CannsimRecord(self.cfg)
        r.stage1_parameter_check()
        self.assertIsNone(r.resolved_cores)


# =============================================================================
# stage2_define_paths
# =============================================================================
class Stage2DefinePathsTests(unittest.TestCase):
    def test_stage2_success(self):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        r = CannsimRecord(cfg)
        with patch.object(_record_mod, "resolve_record_paths") as m_resolve:
            m_resolve.return_value = (
                MagicMock(
                    ascend_toolkit_path=Path("/toolkit"),
                    ca_model_path=Path("/camodel"),
                    target_dir=Path("/tmp/out/npusim_20250101_echo"),
                    record_dir=Path("/tmp/out/npusim_20250101_echo/record"),
                    cannsim_log=Path("/tmp/out/npusim_20250101_echo/npusim.log"),
                ),
                {"USER_APP": "echo hi"},
            )
            self.assertTrue(r.stage2_define_paths())
            self.assertEqual(r.target_dir, Path("/tmp/out/npusim_20250101_echo"))

    def test_stage2_record_path_error(self):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        r = CannsimRecord(cfg)
        from npusim.core.record.paths import RecordPathError

        with patch.object(
            _record_mod, "resolve_record_paths", side_effect=RecordPathError("bad path", {"msg": "test"})
        ):
            self.assertFalse(r.stage2_define_paths())


# =============================================================================
# stage3_execute（需要大量 mock 避免真实的子进程调动）
# =============================================================================
class Stage3ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        self.r = CannsimRecord(self.cfg)
        self.r.target_dir = Path("/tmp/out")
        self.r.record_dir = Path("/tmp/out/record")
        self.r.cannsim_log = Path("/tmp/out/npusim.log")
        self.r.ca_model_path = Path("/tmp/camodel")
        self.r.launch = MagicMock()
        self.r.launch.argv = ["bash", "-c", "echo hi"]
        self.r.launch.cwd = Path("/tmp")
        self.r.launch.asset_roots = (Path("/tmp"),)
        self.r.launch.is_python = False

    def test_stage3_writes_soc_version(self):
        with patch.object(_record_mod, "prepare_runtime_dirs", return_value=True), patch.object(
            _record_mod, "build_execution_env"
        ) as m_bee, patch.object(_record_mod, "run_user_app_with_dashboard") as m_run, patch.object(
            _record_mod, "archive_assets", return_value=True
        ), patch.object(_record_mod, "archive_instr_bin_glob", return_value=True), patch.object(
            _record_mod, "print_stage3_preamble"
        ), patch.object(_record_mod.Path, "mkdir"):
            m_bee.return_value = {}
            m_run.return_value = MagicMock(
                dashboard=None,
                stderr_tail="",
                returncode=0,
                interrupted=False,
                success=True,
            )
            self.r.record_dir = MagicMock()
            self.r.record_dir.__truediv__.return_value = MagicMock()
            ok = self.r.stage3_execute()
            self.assertTrue(ok)

    def test_stage3_failure_archives_assets(self):
        with patch.object(_record_mod, "prepare_runtime_dirs", return_value=True), patch.object(
            _record_mod, "build_execution_env"
        ) as m_bee, patch.object(_record_mod, "run_user_app_with_dashboard") as m_run, patch.object(
            _record_mod, "archive_assets", return_value=False
        ), patch.object(_record_mod, "archive_instr_bin_glob", return_value=True), patch.object(
            _record_mod, "print_stage3_preamble"
        ), patch.object(_record_mod.Path, "mkdir"):
            m_bee.return_value = {}
            m_run.return_value = MagicMock(
                dashboard=None,
                stderr_tail="",
                returncode=1,
                interrupted=False,
                success=False,
            )
            self.r.record_dir = MagicMock()
            self.r.record_dir.__truediv__.return_value = MagicMock()
            ok = self.r.stage3_execute()
            self.assertFalse(ok)

    def test_stage3_keyboard_interrupt(self):
        with patch.object(_record_mod, "prepare_runtime_dirs", return_value=True), patch.object(
            _record_mod, "build_execution_env"
        ) as m_bee, patch.object(
            _record_mod, "run_user_app_with_dashboard", side_effect=KeyboardInterrupt()
        ), patch.object(_record_mod, "print_stage3_preamble"), patch.object(_record_mod.Path, "mkdir"):
            m_bee.return_value = {}
            self.r.record_dir = MagicMock()
            self.r.record_dir.__truediv__.return_value = MagicMock()
            ok = self.r.stage3_execute()
            self.assertFalse(ok)
            self.assertTrue(self.r._interrupted)

    def test_stage3_record_path_error_in_execute_user_app(self):
        from npusim.core.record.paths import RecordPathError

        with patch.object(_record_mod, "build_execution_env", side_effect=RecordPathError("no helper")), patch.object(
            _record_mod.Path, "mkdir"
        ):
            self.r.record_dir = MagicMock()
            self.r.record_dir.__truediv__.return_value = MagicMock()
            ok = self.r.stage3_execute()
            self.assertFalse(ok, "RecordPathError should be caught in _execute_user_app")
            self.assertFalse(self.r._interrupted)


class UserAppMarkerTests(unittest.TestCase):
    def _record(self, raw_app: str):
        cfg = RecordConfig(user_app=raw_app, output=Path("/tmp/out"), soc_version="Ascend950")
        return CannsimRecord(cfg)

    def test_binary_mode_writes_marker(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            app = Path(tmp) / "app"
            app.write_bytes(b"\x7fELFapp")
            r = self._record(str(app))
            record_dir = Path(tmp) / "record"
            record_dir.mkdir()
            r.record_dir = record_dir
            r._write_user_app_marker()
            self.assertEqual((record_dir / ".user-app").read_text(encoding="utf-8").strip(), str(app))

    def test_script_mode_does_not_write_marker(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            app = Path(tmp) / "app.py"
            app.write_text("print('ok')\n", encoding="utf-8")
            r = self._record(str(app))
            record_dir = Path(tmp) / "record"
            record_dir.mkdir()
            r.record_dir = record_dir
            r._write_user_app_marker()
            self.assertFalse((record_dir / ".user-app").exists())


# =============================================================================
# run() + record() 顶层编排
# =============================================================================
class OrchestrationTests(unittest.TestCase):
    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(_record_mod, "print_start_banner")
    @patch.object(_record_mod, "cleanup_internal_dirs")
    def test_record_success_path(self, m_clean, m_banner, m_sleep):
        cfg = RecordConfig(user_app="python3 -c pass", output=Path("/tmp/out"), soc_version="Ascend950")
        with patch.object(CannsimRecord, "run", return_value=True) as m_run, patch.object(
            _record_mod, "generate_auto_report"
        ) as m_gr, patch.object(CannsimRecord, "print_summary") as m_sum:
            ok = record(cfg)
            self.assertTrue(ok)
            m_run.assert_called_once()
            m_sum.assert_called_once_with(True)
            m_gr.assert_not_called()  # gen_report is ""

    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(_record_mod, "print_start_banner")
    @patch.object(_record_mod, "cleanup_internal_dirs")
    def test_record_gen_report_triggers_auto(self, m_clean, m_banner, m_sleep):
        cfg = RecordConfig(
            user_app="python3 -c pass",
            output=Path("/tmp/out"),
            soc_version="Ascend950",
            gen_report="vf",
        )
        with patch.object(CannsimRecord, "run", return_value=True), patch.object(
            _record_mod, "generate_auto_report"
        ), patch.object(CannsimRecord, "print_summary"):
            r = CannsimRecord(cfg)
            r.target_dir = Path("/tmp/out/target")
            r.record_dir = Path("/tmp/out/target/record")
            ok = record(cfg)
            self.assertTrue(ok)

    @patch.object(_record_mod, "_maybe_sleep")
    def test_run_stage1_fails(self, m_sleep):
        cfg = RecordConfig(user_app="", output=Path("/tmp/out"), soc_version="Ascend950")
        executor = CannsimRecord(cfg)
        self.assertFalse(executor.run())

    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(CannsimRecord, "stage1_parameter_check", return_value=True)
    @patch.object(CannsimRecord, "stage2_define_paths", return_value=False)
    def test_run_stage2_fails(self, m_s1, m_s2, m_sleep):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        executor = CannsimRecord(cfg)
        self.assertFalse(executor.run())

    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(CannsimRecord, "stage1_parameter_check", return_value=True)
    @patch.object(CannsimRecord, "stage2_define_paths", return_value=True)
    @patch.object(CannsimRecord, "stage3_execute", return_value=False)
    def test_run_stage3_fails(self, m_s1, m_s2, m_s3, m_sleep):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        executor = CannsimRecord(cfg)
        self.assertFalse(executor.run())

    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(CannsimRecord, "stage1_parameter_check", return_value=True)
    @patch.object(CannsimRecord, "stage2_define_paths", return_value=True)
    @patch.object(CannsimRecord, "stage3_execute", return_value=True)
    def test_run_success(self, m_s1, m_s2, m_s3, m_sleep):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        executor = CannsimRecord(cfg)
        self.assertTrue(executor.run())

    @patch.object(_record_mod, "_maybe_sleep")
    @patch.object(_record_mod, "print_start_banner")
    @patch.object(_record_mod, "cleanup_internal_dirs")
    def test_record_keyboard_interrupt_in_run(self, m_clean, m_banner, m_sleep):
        cfg = RecordConfig(user_app="echo hi", output=Path("/tmp/out"), soc_version="Ascend950")
        with patch.object(CannsimRecord, "run", side_effect=KeyboardInterrupt()), patch.object(
            CannsimRecord, "print_summary"
        ):
            ok = record(cfg)
            self.assertFalse(ok)

    def test_print_summary_success_path(self):
        executor = object.__new__(CannsimRecord)
        executor._interrupted = False
        executor._last_run_elapsed = 5.0
        executor._last_returncode = 0
        executor._last_dashboard = MagicMock()
        executor._last_dashboard.detector.hw_sim_times = [1.23]
        executor._last_dashboard.warnings_summary = []
        executor.target_dir = Path("/tmp/out")
        executor.cannsim_log = Path("/tmp/out/npusim.log")

        out = io.StringIO()
        with redirect_stdout(out):
            executor.print_summary(True)
        text = out.getvalue()
        self.assertIn("SUCCESS", text)
        self.assertIn("hardware sim", text)

    def test_print_summary_failure_with_returncode(self):
        executor = object.__new__(CannsimRecord)
        executor._interrupted = False
        executor._last_run_elapsed = 3.0
        executor._last_returncode = -9
        executor._last_dashboard = MagicMock()
        executor._last_dashboard.detector.hw_sim_times = []
        executor._last_dashboard.warnings_summary = [("segfault", 1)]
        executor._last_stderr_tail = "FATAL: signal 9\n"
        executor.target_dir = Path("/tmp/out")
        executor.cannsim_log = Path("/tmp/out/npusim.log")

        out = io.StringIO()
        with redirect_stdout(out):
            executor.print_summary(False)
        text = out.getvalue()
        self.assertIn("FAILED", text)
        self.assertIn("signal 9", text)
        self.assertIn("segfault", text)
        self.assertIn("FATAL: signal 9", text)


if __name__ == "__main__":
    unittest.main()
