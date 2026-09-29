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
from unittest.mock import patch

from npusim.main import (
    setup_parser,
    handle_record_command,
    handle_report_command,
    main,
)


def _parse(argv):
    from npusim.core.record.config import preprocess_gen_report_argv

    return setup_parser().parse_args(preprocess_gen_report_argv(argv))


class MainParserTests(unittest.TestCase):
    def test_parser_prog(self):
        self.assertEqual(setup_parser().prog, "npusim")

    def test_record_parser_defaults(self):
        args = _parse(["record", "app", "-s", "Ascend950"])
        self.assertEqual(args.command, "record")
        self.assertFalse(args.pv)

    def test_record_parser_with_all_options(self):
        args = _parse(
            ["record", "app", "-s", "Ascend950", "-o", "/tmp/out", "-u", "--opt 1", "-n", "0-2", "--pv", "-g", "vf"]
        )
        self.assertEqual(args.user_app, "app")
        self.assertEqual(args.output, "/tmp/out")
        self.assertEqual(args.core_id, "0-2")
        self.assertTrue(args.pv)

    def test_record_case_dir_parser(self):
        args = _parse(["record", "-c", "/cases/x", "-o", "/tmp/out"])
        self.assertEqual(args.case_dir, "/cases/x")

    def test_report_parser_defaults(self):
        args = _parse(["report", "-e", "/tmp/export"])
        self.assertEqual(args.command, "report")
        self.assertFalse(args.smart_pipes)

    def test_report_parser_full(self):
        args = _parse(
            [
                "report",
                "-e",
                "/tmp/export",
                "-o",
                "/tmp/out",
                "-n",
                "0",
                "--smart-pipes",
                "-m",
                "--object-file",
                "/tmp/app.o",
            ]
        )
        self.assertTrue(args.smart_pipes)
        self.assertTrue(args.merge_trace)
        self.assertEqual(args.object_file, "/tmp/app.o")

    def test_report_export_required(self):
        parser = setup_parser()
        # argparse 报错时以退出码 2 终止；用 BaseException 捕获并验证退出码
        with self.assertRaises(BaseException) as cm:
            parser.parse_args(["report"])
        self.assertEqual(cm.exception.code, 2)


class HandleRecordCommandTests(unittest.TestCase):
    def test_ktest_mode(self):
        args = _parse(["record", "-c", "/cases/x"])
        with patch("npusim.main.ktest", return_value=True):
            rc = handle_record_command(args, setup_parser())
        self.assertEqual(rc, 0)

    def test_ktest_mode_failure(self):
        args = _parse(["record", "-c", "/cases/x"])
        with patch("npusim.main.ktest", return_value=False):
            rc = handle_record_command(args, setup_parser())
        self.assertEqual(rc, 1)

    def test_ktest_with_user_app_rejected(self):
        args = _parse(["record", "app", "-c", "/cases/x"])
        with self.assertRaises(BaseException) as cm:
            handle_record_command(args, setup_parser())
        self.assertEqual(cm.exception.code, 2)

    def test_ktest_with_soc_rejected(self):
        args = _parse(["record", "-c", "/cases/x", "-s", "Ascend950"])
        with self.assertRaises(BaseException) as cm:
            handle_record_command(args, setup_parser())
        self.assertEqual(cm.exception.code, 2)

    def test_record_mode_missing_args(self):
        args = _parse(["record"])
        with self.assertRaises(BaseException) as cm:
            handle_record_command(args, setup_parser())
        self.assertEqual(cm.exception.code, 2)

    def test_record_mode_success(self):
        args = _parse(["record", "app", "-s", "Ascend950"])
        with patch("npusim.main.record", return_value=True) as m_rec:
            rc = handle_record_command(args, setup_parser())
        self.assertEqual(rc, 0)
        self.assertTrue(m_rec.called)

    def test_record_mode_failure(self):
        args = _parse(["record", "app", "-s", "Ascend950"])
        with patch("npusim.main.record", return_value=False):
            rc = handle_record_command(args, setup_parser())
        self.assertEqual(rc, 1)


class HandleReportCommandTests(unittest.TestCase):
    def test_report_success(self):
        args = _parse(["report", "-e", "/tmp/export"])
        with patch("npusim.main.report", return_value=True) as m_rpt:
            rc = handle_report_command(args)
        self.assertEqual(rc, 0)
        m_rpt.assert_called_once()

    def test_report_failure(self):
        args = _parse(["report", "-e", "/tmp/export"])
        with patch("npusim.main.report", return_value=False):
            rc = handle_report_command(args)
        self.assertEqual(rc, 1)

    def test_report_object_file_is_forwarded(self):
        args = _parse(["report", "-e", "/tmp/export", "--object-file", "/tmp/app.o"])
        with patch("npusim.main.report", return_value=True) as m_rpt:
            rc = handle_report_command(args)
        self.assertEqual(rc, 0)
        m_rpt.assert_called_once()
        self.assertEqual(m_rpt.call_args.args[3], "/tmp/app.o")


class MainTests(unittest.TestCase):
    @patch("npusim.main.sys")
    def test_main_no_command_prints_help(self, mock_sys):
        mock_sys.argv = ["npusim"]
        with patch.object(setup_parser(), "print_help"):
            rc = main()
        self.assertEqual(rc, 1)

    @patch("npusim.main.sys")
    def test_main_record_command(self, mock_sys):
        mock_sys.argv = ["npusim", "record", "app", "-s", "Ascend950"]
        with patch("npusim.main.record", return_value=True):
            rc = main()
        self.assertEqual(rc, 0)

    @patch("npusim.main.sys")
    def test_main_report_command(self, mock_sys):
        mock_sys.argv = ["npusim", "report", "-e", "/tmp/export"]
        with patch("npusim.main.report", return_value=True):
            rc = main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
