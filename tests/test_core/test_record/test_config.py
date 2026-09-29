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

from npusim.core.record.config import (
    normalize_gen_report,
    preprocess_gen_report_argv,
    build_record_config_from_args,
)
from npusim.main import setup_parser


def _parse(argv):
    """Parse record argv with the same preprocessing used by the CLI entry point."""
    parser = setup_parser()
    return parser.parse_args(preprocess_gen_report_argv(argv))


def _parse(argv):
    """Parse record argv with the same preprocessing used by the CLI entry point."""

    parser = setup_parser()
    return parser.parse_args(preprocess_gen_report_argv(argv))


class GenReportCliTest(unittest.TestCase):
    def _gen_report(self, argv):
        return normalize_gen_report(_parse(argv).gen_report)

    def test_bare_flag_enables_report(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950", "-g"])
        self.assertEqual(args.user_app, "ops_test")
        self.assertEqual(normalize_gen_report(args.gen_report), "true")

    def test_vf_value_exports_vf_json(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950", "-g", "vf", "-n", "0,1"])
        self.assertEqual(args.user_app, "ops_test")
        self.assertEqual(normalize_gen_report(args.gen_report), "vf")

    def test_vf_value_is_case_insensitive(self):
        self.assertEqual(self._gen_report(["record", "ops_test", "-s", "Ascend950", "-g", "VF"]), "vf")

    def test_gen_report_before_user_app_does_not_swallow_operator_name(self):
        # `-g` written before the positional app (operator name) must not steal it.
        args = _parse(["record", "-s", "Ascend950", "-g", "ops_test", "-n", "0,1"])
        self.assertEqual(args.user_app, "ops_test")
        self.assertEqual(normalize_gen_report(args.gen_report), "true")

    def test_gen_report_before_quoted_command_does_not_swallow_it(self):
        args = _parse(["record", "-s", "Ascend950", "-g", "python3 test_add.py"])
        self.assertEqual(args.user_app, "python3 test_add.py")
        self.assertEqual(normalize_gen_report(args.gen_report), "true")

    def test_gen_report_followed_by_other_option(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950", "-g", "-n", "0,1"])
        self.assertEqual(args.user_app, "ops_test")
        self.assertEqual(args.core_id, "0,1")
        self.assertEqual(normalize_gen_report(args.gen_report), "true")

    def test_long_form_equals_vf(self):
        self.assertEqual(self._gen_report(["record", "ops_test", "-s", "Ascend950", "--gen-report=vf"]), "vf")

    def test_long_form_space_separated_vf(self):
        self.assertEqual(self._gen_report(["record", "ops_test", "-s", "Ascend950", "--gen-report", "vf"]), "vf")

    def test_absent_gen_report(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950"])
        self.assertEqual(normalize_gen_report(args.gen_report), "")


class PvFlagConfigTest(unittest.TestCase):
    def test_build_record_config_defaults_to_ca(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950"])
        cfg = build_record_config_from_args(args)
        self.assertEqual(cfg.model, "CA")

    def test_build_record_config_with_pv(self):
        args = _parse(["record", "ops_test", "-s", "Ascend950", "--pv"])
        cfg = build_record_config_from_args(args)
        self.assertEqual(cfg.model, "PV")


if __name__ == "__main__":
    unittest.main()
