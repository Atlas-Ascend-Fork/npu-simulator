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

from npusim.core.public.soc_info import (
    DEFAULT_CORE_ID,
    SOC_INFO,
    cores_to_mask,
    get_ai_core_count,
    parse_core_id,
)


class SocInfoTests(unittest.TestCase):
    def test_soc_info_has_ascend950(self):
        self.assertIn("Ascend950", SOC_INFO)
        self.assertEqual(SOC_INFO["Ascend950"]["max_cores"], 32)

    def test_get_ai_core_count_known(self):
        self.assertEqual(get_ai_core_count("Ascend950"), 32)

    def test_get_ai_core_count_unknown_raises(self):
        with self.assertRaises(ValueError):
            get_ai_core_count("UnknownSoc")

    def test_parse_core_id_none_returns_default(self):
        self.assertEqual(parse_core_id(None, 32), DEFAULT_CORE_ID)

    def test_parse_core_id_empty_string(self):
        self.assertEqual(parse_core_id("", 32), DEFAULT_CORE_ID)

    def test_parse_core_id_single(self):
        self.assertEqual(parse_core_id("5", 32), (5,))

    def test_parse_core_id_all(self):
        self.assertEqual(parse_core_id("all", 32), tuple(range(32)))

    def test_parse_core_id_ranges(self):
        self.assertEqual(parse_core_id("0-2,12-14", 32), (0, 1, 2, 12, 13, 14))

    def test_parse_core_id_out_of_bounds(self):
        with self.assertRaises(ValueError):
            parse_core_id("32", 32)

    def test_parse_core_id_invalid_format(self):
        with self.assertRaises(ValueError):
            parse_core_id("abc", 32)

    def test_parse_core_id_zero_max_cores(self):
        with self.assertRaises(ValueError):
            parse_core_id("0", 0)

    def test_cores_to_mask(self):
        self.assertEqual(cores_to_mask((0, 2, 3)), 0b1101)

    def test_cores_to_mask_empty(self):
        self.assertEqual(cores_to_mask(()), 0)


if __name__ == "__main__":
    unittest.main()
