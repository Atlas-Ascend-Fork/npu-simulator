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

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from npusim.core.record.paths import resolve_record_paths
from npusim.core.record.runtime import build_execution_env


class RecordPathsTest(unittest.TestCase):
    def test_record_outputs_use_npusim_public_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            toolkit = root / "toolkit"
            (toolkit / "tools/simulator/Ascend950PR_9589/camodel").mkdir(parents=True)
            launch = SimpleNamespace(
                label="vector_add",
                raw="./vector_add",
                kind="binary",
                cwd=root,
                executable_path=root / "vector_add",
            )

            with patch.dict(os.environ, {"ASCEND_TOOLKIT_HOME": str(toolkit)}):
                paths, _ = resolve_record_paths(root / "output", "Ascend950", launch)

            self.assertTrue(paths.target_dir.name.startswith("npusim_"))
            self.assertTrue(paths.target_dir.name.endswith("_vector_add"))
            self.assertEqual(paths.cannsim_log.name, "npusim.log")

    def test_runtime_enables_helper_from_simulator_bin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            toolkit = root / "toolkit"
            helper = toolkit / "tools/simulator/bin/libcannsim_helper.so"
            helper.parent.mkdir(parents=True)
            helper.write_text("", encoding="utf-8")
            ca_model_path = toolkit / "tools/simulator/Ascend950PR_9589/camodel"
            ca_model_path.mkdir(parents=True)
            record_dir = root / "record"
            record_dir.mkdir()

            env = build_execution_env(
                {"ASCEND_TOOLKIT_HOME": str(toolkit)},
                ca_model_path,
                record_dir,
                "Ascend950",
                resolved_cores=None,
                is_python_launch=False,
            )

            self.assertTrue(env["LD_PRELOAD"].startswith(str(helper.resolve())))
            self.assertEqual(env["CANNSIM_HELPER_LAUNCH_DB_DIR"], str(record_dir))
            self.assertEqual(env["CANNSIM_REPORT_ARCH"], "v100")
