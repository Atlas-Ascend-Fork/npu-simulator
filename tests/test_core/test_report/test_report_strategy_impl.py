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
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from npusim.core.report.report_strategy_impl import (
    FallbackChain,
    BiProfModuleStrategy,
)


class ReportStrategyImplTests(unittest.TestCase):
    def test_fallback_chain_empty(self):
        chain = FallbackChain([])
        with TemporaryDirectory() as tmp:
            backend, ok = chain.execute([], Path(tmp), Path(tmp), ())
            self.assertEqual(backend, "")
            self.assertFalse(ok)
        err = io.StringIO()
        with redirect_stderr(err):
            backend, ok = chain.execute([], Path(tmp), Path(tmp), ())
        self.assertEqual(backend, "")
        self.assertFalse(ok)
        self.assertIn("No report backend succeeded.", err.getvalue())

    def test_bi_prof_module_strategy_name(self):
        strategy = BiProfModuleStrategy()
        self.assertEqual(strategy.name, "BiProfRunner module")

    def test_report_chain_is_fallback_chain(self):
        from npusim.core.report.report_strategy_impl import REPORT_CHAIN

        self.assertIsInstance(REPORT_CHAIN, FallbackChain)

    def test_no_backend_message_does_not_reference_cannprof_binary(self):
        err = io.StringIO()
        with redirect_stderr(err):
            backend, ok = FallbackChain([]).execute(
                [],
                export_abs=Path("/tmp"),
                output_abs=Path("/tmp"),
                core_ids=(0,),
            )

        self.assertEqual(backend, "")
        self.assertFalse(ok)
        text = err.getvalue()
        self.assertIn("prof_demo", text)
        self.assertNotIn("cannprof", text)
        self.assertNotIn("build_cannprof.sh", text)


if __name__ == "__main__":
    unittest.main()
