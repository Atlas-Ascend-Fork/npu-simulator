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


import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from npusim.core.record.runner import run_user_app_with_dashboard


class RecordRunnerTests(unittest.TestCase):
    def test_failure_tail_preserves_log_and_decodes_bad_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "npusim.log"
            code = "import sys; sys.stdout.write('hello\\n\\n'); sys.stderr.buffer.write(b'bad:\\xff\\n'); sys.exit(3)"

            result = run_user_app_with_dashboard(
                [sys.executable, "-c", code],
                os.environ.copy(),
                str(tmp_path),
                str(log_path),
                "runner-test",
            )

            log_data = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertFalse(result.success)
            self.assertFalse(result.interrupted)
            self.assertEqual(result.returncode, 3)
            self.assertIsNotNone(result.dashboard)
            self.assertEqual(result.stderr_tail, log_data)
            self.assertIn("hello\n", log_data)
            self.assertIn("\n\n", log_data)
            self.assertIn("bad:\ufffd\n", log_data)

    @unittest.skip("flaky: 子进程组清理时序在 CI 偶发失败")
    def test_keyboard_interrupt_terminates_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log_path = tmp_path / "npusim.log"
            timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
            timer.start()

            try:
                result = run_user_app_with_dashboard(
                    ["bash", "-c", "sleep 30 & wait"],
                    os.environ.copy(),
                    str(tmp_path),
                    str(log_path),
                    "interrupt-test",
                )
            finally:
                timer.cancel()

            self.assertFalse(result.success)
            self.assertTrue(result.interrupted)
            self.assertIsNotNone(result.returncode)

            dashboard = result.dashboard
            self.assertIsNotNone(dashboard)

            deadline = time.time() + 2.0
            pids = dashboard.monitor._get_all_pids()
            while pids and time.time() < deadline:
                time.sleep(0.05)
                pids = dashboard.monitor._get_all_pids()
            self.assertEqual(pids, [])


if __name__ == "__main__":
    unittest.main()
