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

from npusim.core.record.auto_report import generate_auto_report

# py3.10 的 mock 逐级 getattr 解析字符串目标，会命中
# cannsim/core/__init__.py 里 from-import 遮蔽同名子包的函数，
# 因此统一改用 patch.object + 模块对象。
_auto_report_mod = importlib.import_module("npusim.core.record.auto_report")


class AutoReportTests(unittest.TestCase):
    @patch.object(_auto_report_mod, "report", return_value=True)
    def test_generate_auto_report_success(self, mock_report):
        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp)
            target_dir = record_dir / "target"
            target_dir.mkdir()
            ok = generate_auto_report(record_dir, target_dir, gen_report="true", core_id="0")
            self.assertTrue(ok)
            mock_report.assert_called_once()
            _, kwargs = mock_report.call_args
            self.assertEqual(kwargs["core_id"], "0")

    @patch.object(_auto_report_mod, "report", return_value=True)
    def test_generate_auto_report_vf_sets_export_vf(self, mock_report):
        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp)
            target_dir = record_dir / "target"
            target_dir.mkdir()
            ok = generate_auto_report(record_dir, target_dir, gen_report="vf", core_id="0")
            self.assertTrue(ok)
            _, kwargs = mock_report.call_args
            self.assertTrue(kwargs["export_vf"])

    @patch.object(_auto_report_mod, "report", return_value=True)
    def test_generate_auto_report_empty_core_id_defaults_to_0(self, mock_report):
        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp)
            target_dir = record_dir / "target"
            target_dir.mkdir()
            ok = generate_auto_report(record_dir, target_dir, gen_report="true", core_id="")
            self.assertTrue(ok)
            _, kwargs = mock_report.call_args
            self.assertEqual(kwargs["core_id"], "0")

    @patch.object(_auto_report_mod, "report", return_value=False)
    def test_generate_auto_report_failure(self, mock_report):
        with tempfile.TemporaryDirectory() as tmp:
            record_dir = Path(tmp)
            target_dir = record_dir / "target"
            target_dir.mkdir()
            ok = generate_auto_report(record_dir, target_dir, gen_report="true", core_id="0")
            self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
