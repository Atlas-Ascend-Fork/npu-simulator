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
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.record.runtime import build_execution_env as record_build_env
from npusim.core.record.runtime import _camodel_template_dir, _CAMODEL_TEMPLATES_DIR, apply_cc_env

runtime_mod = importlib.import_module("npusim.core.record.runtime")


class CamodelTemplateDirTests(unittest.TestCase):
    def test_prefers_module_relative_templates(self):
        with tempfile.TemporaryDirectory() as tmp:
            toolkit = Path(tmp) / "toolkit"
            self.assertEqual(_camodel_template_dir(str(toolkit)), _CAMODEL_TEMPLATES_DIR)

    def test_falls_back_to_legacy_cannsim_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "toolkit" / "python/site-packages/cannsim/core/record/ktest/templates"
            legacy.mkdir(parents=True)
            with patch.object(runtime_mod, "_CAMODEL_TEMPLATES_DIR", Path("/nonexistent")):
                self.assertEqual(_camodel_template_dir(str(Path(tmp) / "toolkit")), legacy)

    def test_apply_cc_env_selects_module_relative_template(self):
        env = {"ASCEND_TOOLKIT_HOME": "/tmp/fake-toolkit"}
        apply_cc_env(env, 2, "Ascend950")
        self.assertTrue(env["CAMODEL_CONFIG_PATH"].endswith("camodel_v100_2p.json"))
        self.assertIn(str(_CAMODEL_TEMPLATES_DIR), env["CAMODEL_CONFIG_PATH"])


class RecordRuntimeTests(unittest.TestCase):
    def _fake_helper_paths(self, tmp):
        root = Path(tmp)
        toolkit = root / "toolkit"
        helper = toolkit / "tools/simulator/bin/libcannsim_helper.so"
        helper.parent.mkdir(parents=True)
        helper.write_text("", encoding="utf-8")
        camodel = toolkit / "tools/simulator/Ascend950PR_9589/camodel"
        camodel.mkdir(parents=True)
        record_dir = root / "record"
        record_dir.mkdir()
        return {"ASCEND_TOOLKIT_HOME": str(toolkit)}, camodel, record_dir

    def test_record_env_sets_camodel_sim_type_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, camodel, record_dir = self._fake_helper_paths(tmp)
            env = record_build_env(base, camodel, record_dir, "Ascend950", None, False)
            self.assertEqual(env["CAMODEL_SIM_TYPE"], "CA")

    def test_record_env_sets_camodel_sim_type_pv(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, camodel, record_dir = self._fake_helper_paths(tmp)
            env = record_build_env(base, camodel, record_dir, "Ascend950", None, False, model="PV")
            self.assertEqual(env["CAMODEL_SIM_TYPE"], "PV")

    def test_record_env_sets_core_enable_mask(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, camodel, record_dir = self._fake_helper_paths(tmp)
            env = record_build_env(base, camodel, record_dir, "Ascend950", (0, 2), False)
            self.assertEqual(env["CORE_ENABLE_MASK"], "0x5")

    def test_record_env_sets_ld_library_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, camodel, record_dir = self._fake_helper_paths(tmp)
            env = record_build_env(base, camodel, record_dir, "Ascend950", None, False)
            self.assertTrue(env["LD_LIBRARY_PATH"].startswith(str(camodel)))

    def test_record_env_python_launch_sets_task_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            base, camodel, record_dir = self._fake_helper_paths(tmp)
            env = record_build_env(base, camodel, record_dir, "Ascend950", None, True)
            self.assertEqual(env["TASK_QUEUE_ENABLE"], "0")


class RuntimeDirsTests(unittest.TestCase):
    def test_prepare_runtime_dirs_leaves_user_cwd_untouched(self):
        """回归 #29：不得在用户工作目录下创建 log/ run_log/（会被事后搬走并删除）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_cwd = root / "work"
            app_cwd.mkdir()
            target_dir = root / "out"

            self.assertTrue(runtime_mod.prepare_runtime_dirs(app_cwd, target_dir))
            self.assertEqual(list(app_cwd.iterdir()), [])
            self.assertTrue((target_dir / "record" / "log_ca").is_dir())

    def test_prepare_runtime_dirs_keeps_existing_user_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_cwd = root / "work"
            (app_cwd / "log").mkdir(parents=True)
            (app_cwd / "log" / "mine.txt").write_text("mine", encoding="utf-8")
            (app_cwd / "run_log").mkdir()

            self.assertTrue(runtime_mod.prepare_runtime_dirs(app_cwd, root / "out"))
            self.assertTrue((app_cwd / "log" / "mine.txt").is_file())
            self.assertFalse((app_cwd / "log" / "ub_log").exists())
            self.assertEqual(list((app_cwd / "run_log").iterdir()), [])

    def test_camodel_assets_do_not_include_user_cwd_dirs(self):
        self.assertNotIn("log", runtime_mod.CAMODEL_ASSETS)
        self.assertNotIn("run_log", runtime_mod.CAMODEL_ASSETS)
        self.assertIn("stars_log*_task.dump", runtime_mod.CAMODEL_ASSETS)


if __name__ == "__main__":
    unittest.main()
