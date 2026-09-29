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


import io
import unittest
import unittest.mock
from contextlib import redirect_stdout, redirect_stderr

from npusim.core.public.comm import (
    print_error,
    print_info,
    print_warn,
    print_debug,
    print_simple_table,
    print_title,
    print_typical_operator_times,
    render_reference_benchmarks,
    _get_style,
    _format_message,
)


class CommTests(unittest.TestCase):
    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", True)
    def test_get_style_error(self):
        pre, suf = _get_style("ERROR")
        self.assertIn("[1m", pre)
        self.assertIn("[0m", suf)

    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", True)
    def test_get_style_warn(self):
        pre, suf = _get_style("WARN")
        self.assertIn("[1m", pre)
        self.assertIn("[93m", pre)

    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", True)
    def test_get_style_info(self):
        pre, suf = _get_style("INFO")
        self.assertIn("[92m", pre)

    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", True)
    def test_get_style_debug(self):
        pre, suf = _get_style("DEBUG")
        self.assertIn("[90m", pre)

    def test_get_style_unknown(self):
        pre, suf = _get_style("TRACE")
        self.assertEqual(pre, "")
        self.assertEqual(suf, "")

    def test_format_message(self):
        msg = _format_message("INFO", "test")
        self.assertIn("INFO", msg)
        self.assertIn("test", msg)

    # ── print_X capture ──
    def test_print_info(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_info("hello info")
        self.assertIn("hello info", out.getvalue())

    def test_print_warn(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_warn("hello warn")
        self.assertIn("hello warn", out.getvalue())

    def test_print_error(self):
        out = io.StringIO()
        with redirect_stderr(out):
            print_error("hello error")
        self.assertIn("hello error", out.getvalue())

    def test_print_debug(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_debug("hello debug")
        self.assertIn("hello debug", out.getvalue())

    # ── print_simple_table ──
    def test_simple_table_dict(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({"a": 1, "b": 2}, header=False)
        self.assertIn("a", out.getvalue())
        self.assertIn("1", out.getvalue())

    def test_simple_table_with_header(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({"k1": "v1", "k2": "v2"}, header=True)
        self.assertIn("k1", out.getvalue())

    def test_simple_table_with_headers_tuple(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({"x": 10}, headers=("Key", "Value"))
        self.assertIn("Key", out.getvalue())
        self.assertIn("Value", out.getvalue())
        self.assertIn("x", out.getvalue())

    def test_simple_table_empty(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({}, header=False)
        self.assertEqual(out.getvalue(), "")

    def test_simple_table_header_none(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({"first": 1, "second": 2}, header=True)
        self.assertIn("first", out.getvalue())
        self.assertIn("second", out.getvalue())

    def test_simple_table_headers_only_first_item(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_simple_table({"h": "1", "k2": "v2"}, header=True)
        self.assertIn("h", out.getvalue())

    # ── print_title ──
    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", False)
    def test_print_title(self):
        out = io.StringIO()
        with redirect_stdout(out):
            print_title("Section 1")
        self.assertIn("Section 1", out.getvalue())
        self.assertIn("Section 1", out.getvalue())
        self.assertIn("╔", out.getvalue())

    # ── print_typical_operator_times ──
    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", False)
    def test_print_typical_operator_times(self):
        value = print_typical_operator_times()
        self.assertIn("No operator time", value)

    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", False)
    def test_print_typical_operator_times_with_custom(self):
        data = [
            {
                "Operator type": "CUBE",
                "Case name": "Matmul",
                "Parameters": "M=16",
                "Cycles": 100,
                "Simulation time(ms)": 50,
            }
        ]
        value = print_typical_operator_times(data)
        self.assertIn("CUBE", value)

    # ── render_reference_benchmarks ──
    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", False)
    def test_render_reference_benchmarks(self):
        from npusim.core.public.typical_operator_times import TYPICAL_OPERATOR_TIMES

        result = render_reference_benchmarks(TYPICAL_OPERATOR_TIMES)
        self.assertIn("Operator type", result)

    @unittest.mock.patch("npusim.core.public.comm.USE_COLOR", False)
    def test_render_reference_benchmarks_with_custom(self):
        data = [
            {"Operator type": "CV", "Case name": "test", "Parameters": "p1", "Simulation time(ms)": 100},
            {"Operator type": "CV", "Case name": "test2", "Parameters": "p2", "Simulation time(ms)": 200},
        ]
        result = render_reference_benchmarks(data)
        self.assertIn("CV", result)
        self.assertIn("0.1s", result)


if __name__ == "__main__":
    unittest.main()
