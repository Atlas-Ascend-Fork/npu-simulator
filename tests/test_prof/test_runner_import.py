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

"""Import smoke tests for the full report engine (stdlib-only)."""

import unittest


class RunnerImportTests(unittest.TestCase):
    def test_runner_importable_and_run_callable(self):
        from npusim.prof.src.runner import BiProfRunner

        self.assertTrue(callable(BiProfRunner.run))

    def test_runner_writes_under_results_subdir(self):
        """Pin the repo-#1 output layout contract.

        npusim post-processing (runner.py.log extraction, prof_demo parity and
        the documented ``report/results/`` layout) requires BiProfRunner to
        archive its output under ``{output}/results``.
        """
        import inspect

        from npusim.prof.src.runner import BiProfRunner

        source = inspect.getsource(BiProfRunner.init)
        self.assertIn('"results"', source)
        self.assertIn("os.path.join(args.output, result_name)", source)

    def test_get_biprof_runner_module_returns_runner(self):
        from npusim.core.report.report_strategy_base import get_biprof_runner_module
        from npusim.prof.src.runner import BiProfRunner

        self.assertIs(get_biprof_runner_module(), BiProfRunner)


if __name__ == "__main__":
    unittest.main()
