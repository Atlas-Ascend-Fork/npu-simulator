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

from npusim.core.record.runtime import build_execution_env as record_build_env


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


if __name__ == "__main__":
    unittest.main()
