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

from npusim.core.report.report_strategy_base import (
    SOC_TO_ARCH,
    get_biprof_runner_module,
)


class ReportStrategyBaseTests(unittest.TestCase):
    def test_soc_to_arch_has_ascend950(self):
        self.assertEqual(SOC_TO_ARCH["Ascend950"], "v100")

    def test_soc_to_arch_contains_ascend950(self):
        self.assertIn("Ascend950", SOC_TO_ARCH)

    def test_get_biprof_runner_module_returns_module_or_none(self):
        mod = get_biprof_runner_module()
        self.assertTrue(mod is None or hasattr(mod, "run"))


if __name__ == "__main__":
    unittest.main()
