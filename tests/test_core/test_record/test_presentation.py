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

from npusim.core.record.presentation import (
    fmt_accent,
    fmt_returncode,
    now_local_str,
)


class PresentationTests(unittest.TestCase):
    def test_fmt_accent_no_color_returns_text(self):
        self.assertEqual(fmt_accent("hello", ""), "hello")

    def test_fmt_accent_with_color(self):
        result = fmt_accent("hello", "\033[96m")
        self.assertIn("hello", result)

    def test_fmt_returncode_none(self):
        self.assertEqual(fmt_returncode(None), "")

    def test_fmt_returncode_exit(self):
        self.assertEqual(fmt_returncode(3), "exit 3")

    def test_fmt_returncode_signal(self):
        self.assertEqual(fmt_returncode(-15), "signal 15")

    def test_now_local_str(self):
        value = now_local_str()
        self.assertRegex(value, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


if __name__ == "__main__":
    unittest.main()
