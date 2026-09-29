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


import unittest
from pathlib import Path
from npusim.core.record.ktest.config import KtestConfig


class KtestConfigTests(unittest.TestCase):
    def test_default_values(self):
        cfg = KtestConfig(case_dir="/cases/test")
        self.assertEqual(cfg.case_dir, "/cases/test")
        self.assertEqual(cfg.output, Path("."))
        self.assertEqual(cfg.gen_report, "")
        self.assertEqual(cfg.model, "CA")

    def test_with_all_fields(self):
        cfg = KtestConfig(case_dir="/cases/a", output=Path("/out"), gen_report="vf", model="PV")
        self.assertEqual(cfg.gen_report, "vf")
        self.assertEqual(cfg.model, "PV")


if __name__ == "__main__":
    unittest.main()
