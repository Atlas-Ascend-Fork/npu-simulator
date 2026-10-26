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

"""Tests for the restored BiProf-first / prof_demo-fallback report chain."""

import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.report import report_strategy_impl
from npusim.core.report.report_strategy_impl import (
    BiProfModuleStrategy,
    FallbackChain,
    ProfDemoStrategy,
    REPORT_CHAIN,
)


class _StubStrategy:
    def __init__(self, name, available, result=(None, True)):
        self._name = name
        self._available = available
        self._result = result
        self.executed = False

    @property
    def name(self):
        return self._name

    def is_available(self):
        return self._available

    def execute(self, biprof_args, export_abs, output_abs, core_ids):
        self.executed = True
        return self._result


class ReportChainTests(unittest.TestCase):
    def test_chain_order_biprof_first_prof_demo_fallback(self):
        strategies = REPORT_CHAIN._strategies
        self.assertEqual(len(strategies), 2)
        self.assertIsInstance(strategies[0], BiProfModuleStrategy)
        self.assertIsInstance(strategies[1], ProfDemoStrategy)

    def test_backend_names_are_distinct_and_registered_for_reorganize(self):
        """report.py keys post-processing on backend names; both reorganize,
        but only BiProf triggers npu-rep export."""
        from npusim.core.report.report_strategy_base import (
            BIPROF_BACKEND_NAME,
            PROF_DEMO_BACKEND_NAME,
            REORGANIZE_BACKENDS,
        )

        self.assertEqual(BiProfModuleStrategy().name, BIPROF_BACKEND_NAME)
        self.assertEqual(ProfDemoStrategy().name, PROF_DEMO_BACKEND_NAME)
        self.assertNotEqual(BIPROF_BACKEND_NAME, PROF_DEMO_BACKEND_NAME)
        self.assertEqual(REORGANIZE_BACKENDS, {BIPROF_BACKEND_NAME, PROF_DEMO_BACKEND_NAME})

    def test_biprof_unavailable_falls_back_to_prof_demo(self):
        with patch.object(
            report_strategy_impl,
            "get_biprof_runner_module",
            return_value=None,
        ):
            self.assertFalse(BiProfModuleStrategy().is_available())
            self.assertTrue(ProfDemoStrategy().is_available())

    def test_fallback_chain_short_circuits_on_first_success(self):
        first = _StubStrategy("first", available=True, result=("first", True))
        second = _StubStrategy("second", available=True, result=("second", True))
        backend, ok = FallbackChain([first, second]).execute([], Path("/tmp"), Path("/tmp"), ())
        self.assertTrue(ok)
        self.assertEqual(backend, "first")
        self.assertFalse(second.executed)

    def test_fallback_chain_skips_unavailable_and_uses_next(self):
        broken = _StubStrategy("broken", available=True, result=("broken", False))
        unavailable = _StubStrategy("unavailable", available=False)
        working = _StubStrategy("working", available=True, result=("working", True))
        backend, ok = FallbackChain([broken, unavailable, working]).execute([], Path("/tmp"), Path("/tmp"), ())
        self.assertTrue(ok)
        self.assertEqual(backend, "working")
        self.assertFalse(unavailable.executed)


if __name__ == "__main__":
    unittest.main()
