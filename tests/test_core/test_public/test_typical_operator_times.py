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

from npusim.core.public.typical_operator_times import TYPICAL_OPERATOR_TIMES


class TypicalOperatorTimesTests(unittest.TestCase):
    def test_has_entries(self):
        self.assertGreater(len(TYPICAL_OPERATOR_TIMES), 0)

    def test_entries_have_required_keys(self):
        required = {"Operator type", "Case name", "Parameters", "Cycles", "Simulation time(ms)"}
        for entry in TYPICAL_OPERATOR_TIMES:
            self.assertTrue(required.issubset(entry.keys()))

    def test_entries_have_positive_cycles(self):
        for entry in TYPICAL_OPERATOR_TIMES:
            self.assertGreater(entry["Cycles"], 0)

    def test_entries_have_positive_sim_time(self):
        for entry in TYPICAL_OPERATOR_TIMES:
            self.assertGreater(entry["Simulation time(ms)"], 0)


if __name__ == "__main__":
    unittest.main()
