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

"""Contract tests between npusim.core report argv and npusim.prof arg parser.

``npusim report`` / ``npusim record -g`` drive BiProfRunner through
``_build_biprof_args``; these tests pin the exact argv shape the ported
npu_emulate parser must keep accepting.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from npusim.core.report.report_path import _build_biprof_args
from npusim.prof.src.common.args import BiProfArgParser


class BiProfArgParserContractTests(unittest.TestCase):
    def setUp(self):
        # BIPROF_OPTS would inject extra options during parse(); keep it out.
        self._saved_biprof_opts = os.environ.get("BIPROF_OPTS")
        os.environ.pop("BIPROF_OPTS", None)

    def tearDown(self):
        if self._saved_biprof_opts is None:
            os.environ.pop("BIPROF_OPTS", None)
        else:
            os.environ["BIPROF_OPTS"] = self._saved_biprof_opts

    def _parse(self, argv):
        parsed = BiProfArgParser().parse(argv)
        self.assertIsNotNone(parsed, "parser rejected argv built by _build_biprof_args")
        return parsed

    def test_full_single_card_argv(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "0000_kernel.db"
            out = Path(tmp) / "reports"
            argv = _build_biprof_args(
                out,
                input_db_path=db,
                core_id="0",
                vf_report_json=True,
                smart_pipes=True,
            )
            parsed = self._parse(argv)
            self.assertEqual(parsed.mode, "report")
            self.assertEqual(parsed.arch, "v100")
            self.assertEqual(parsed.input_db, str(db))
            self.assertEqual(parsed.output, str(out))
            self.assertEqual(parsed.core_id, "0")
            self.assertEqual(parsed.jobs, 8)  # "-j 8" single token must parse
            self.assertTrue(parsed.scalar_ipc)
            self.assertTrue(parsed.vec_ipc)
            self.assertTrue(parsed.smart_pipes)
            self.assertTrue(parsed.vf_report_json)

    def test_multi_chip_argv_strips_ipc_flags(self):
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "0000_kernel.db"
            argv = _build_biprof_args(
                Path(tmp) / "out",
                input_db_path=db,
                core_id="all",
                multi_chip=True,
            )
            parsed = self._parse(argv)
            self.assertFalse(parsed.scalar_ipc)
            self.assertFalse(parsed.vec_ipc)
            self.assertEqual(parsed.core_id, "all")

    def test_input_db_is_required(self):
        with self.assertRaises(SystemExit):
            BiProfArgParser().parse(["report", "-o", "/tmp/out"])

    def test_arch_defaults_to_none_and_db_input_mandatory_flag_exists(self):
        with TemporaryDirectory() as tmp:
            argv = [
                "report",
                "-o",
                str(Path(tmp) / "out"),
                "--input-db",
                str(Path(tmp) / "db.sqlite"),
            ]
            parsed = self._parse(argv)
            # arch is optional in the ported tool (derived from the contract DB)
            self.assertIsNone(parsed.arch)
            self.assertEqual(parsed.input_db, str(Path(tmp) / "db.sqlite"))


if __name__ == "__main__":
    unittest.main()
