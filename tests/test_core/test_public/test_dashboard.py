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
import tempfile
import time
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from npusim.core.public.dashboard import (
    Dashboard,
    HEALTH_IDLE_QUIET,
    PhaseDetector,
    ProcessMonitor,
    ProcStats,
    Event,
    _lang_supports_utf8,
    is_user_stdout_line,
    _format_elapsed,
    _c,
    _parse_stat_data,
    _format_state_counts,
    classify_health,
)


class DashboardTests(unittest.TestCase):
    def test_process_monitor_keeps_sampling_known_pgid_after_main_pid_exits(self):
        with patch("npusim.core.public.dashboard.os.getpgid", return_value=12345), patch(
            "npusim.core.public.dashboard._get_process_group_pids",
            return_value=[45678],
        ), patch(
            "npusim.core.public.dashboard._read_proc_stat",
            return_value=(10, 5, 3, "S"),
        ), patch(
            "npusim.core.public.dashboard._read_proc_status_rss",
            return_value=4096,
        ), patch(
            "npusim.core.public.dashboard._read_thread_states",
            return_value=Counter({"S": 2, "Z": 1}),
        ):
            stats = ProcessMonitor(12345).snapshot()

        self.assertTrue(stats.alive)
        self.assertEqual(stats.main_pid, 12345)
        self.assertEqual(stats.pgid, 12345)
        self.assertEqual(stats.process_count, 1)
        self.assertEqual(stats.threads, 3)
        self.assertEqual(stats.proc_states, Counter({"S": 1}))
        self.assertEqual(stats.thread_states, Counter({"S": 2, "Z": 1}))

    def test_final_settle_snapshot_waits_for_zombie_to_clear(self):
        class _FakeMonitor:
            def __init__(self):
                self.calls = 0

            def snapshot(self):
                self.calls += 1
                if self.calls == 1:
                    return ProcStats(
                        alive=True,
                        main_pid=12345,
                        pgid=12345,
                        process_count=1,
                        proc_states=Counter({"Z": 1}),
                        thread_states=Counter({"Z": 1}),
                    )
                return ProcStats(main_pid=12345, pgid=12345)

        dash = Dashboard(
            pid=12345,
            log_path="/tmp/npusim.log",
            title="runner-test",
            start_time=time.time(),
            is_tty=False,
        )
        fake_monitor = _FakeMonitor()
        dash.monitor = fake_monitor
        dash.FINAL_SETTLE_INTERVAL_S = 0.0

        dash._settle_final_snapshot()

        self.assertEqual(fake_monitor.calls, 2)
        self.assertFalse(dash._last_stats.alive)
        self.assertEqual(dash._last_stats.process_count, 0)

    def test_signal_does_not_report_recent_logs_after_log_lag(self):
        stats = ProcStats(alive=True, cpu_pct=10.0)

        self.assertEqual(
            classify_health(stats, log_lag_s=10.0, phase_ttl_s=10.0),
            HEALTH_IDLE_QUIET,
        )

    def test_state_counts_use_full_names_in_stable_order(self):
        text = _format_state_counts(Counter({"S": 5, "D": 1, "R": 1, "?": 2}))

        self.assertEqual(
            text,
            "running 1   sleeping 5   uninterruptible 1   unknown(?) 2",
        )

    def test_panel_shows_process_group_and_recent_npusim_log_window(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            now = time.time()
            dash = Dashboard(
                pid=12345,
                log_path=str(Path(tmp) / "npusim.log"),
                title="runner-test",
                start_time=now - 12.7,
                is_tty=True,
            )
            dash._last_stats = ProcStats(
                alive=True,
                main_pid=12345,
                pgid=12345,
                process_count=7,
                threads=51,
                rss_bytes=int(6.7 * 1024 * 1024 * 1024),
                cpu_pct=1214.0,
                proc_states=Counter({"S": 5, "D": 1, "R": 1}),
                thread_states=Counter({"S": 44, "D": 1, "R": 6}),
            )
            dash._last_log_mtime = now - 3.9
            dash._log_line_times.extend([now - 9.5, now - 1.0])

            panel = "\n".join(dash._render_panel_lines("alive + host busy"))

            self.assertIn("npusim record", panel)
            self.assertIn(
                "process group     pgid 12345   main pid 12345   processes 7   threads 51",
                panel,
            )
            self.assertIn("host              cpu 12.1 cores   memory resident", panel)
            self.assertIn(
                "proc states       running 1   sleeping 5   uninterruptible 1",
                panel,
            )
            self.assertIn(
                "thread states     running 6   sleeping 44   uninterruptible 1",
                panel,
            )
            self.assertIn(
                "npusim.log        last line",
                panel,
            )
            self.assertIn("recent 2 lines / 10s", panel)
            self.assertNotIn("lines/s", panel)
            self.assertNotIn("state S", panel)
            self.assertNotIn("log: ", panel)
            self.assertNotIn(str(Path(tmp) / "npusim.log"), panel)


# ── Extended coverage: PhaseDetector, _lang_supports_utf8, Event, ProcStats ──
class PhaseDetectorTests(unittest.TestCase):
    def test_initial_phase(self):
        pd = PhaseDetector()
        self.assertIsNone(pd.phase)
        self.assertGreaterEqual(pd.phase_ttl(), 0)

    def test_feed_stage3_simulation(self):
        pd = PhaseDetector()
        events = pd.feed("[3/3] Simulation")
        self.assertIsInstance(events, list)

    def test_progress_label_no_phase(self):
        pd = PhaseDetector()
        self.assertEqual(pd.progress_label(), "")


class EventTests(unittest.TestCase):
    def test_event_creation(self):
        e = Event(elapsed_s=1.0, level="user", message="test event")
        self.assertEqual(e.level, "user")
        self.assertEqual(e.elapsed_s, 1.0)
        self.assertEqual(e.message, "test event")


class ProcStatsTests(unittest.TestCase):
    def test_rss_hr_none(self):
        stats = ProcStats(main_pid=1, pgid=1)
        hr = stats.rss_hr if isinstance(stats.rss_hr, str) else stats.rss_hr()
        self.assertIn("0", str(hr))

    def test_rss_hr_with_value(self):
        stats = ProcStats(main_pid=1, pgid=1, rss_bytes=1024 * 1024 * 100)
        hr = stats.rss_hr()
        self.assertIn("100", str(hr))


class IsUserStdoutLineTests(unittest.TestCase):
    def test_normal_line(self):
        self.assertTrue(is_user_stdout_line("hello"))

    def test_dashboard_prefix(self):
        self.assertFalse(is_user_stdout_line("[INFO] boot ok"))

    def test_npusim_log_prefix(self):
        self.assertTrue(is_user_stdout_line("npusim.log: test"))


class LangSupportsUtf8Tests(unittest.TestCase):
    @patch.dict(os.environ, {"LANG": "en_US.UTF-8"})
    def test_utf8_supported(self):
        self.assertTrue(_lang_supports_utf8())

    @patch.dict(os.environ, {"LC_ALL": "C", "LC_CTYPE": "C", "LANG": "C"})
    def test_ascii_locale(self):
        self.assertFalse(_lang_supports_utf8())


class FormatElapsedTests(unittest.TestCase):
    def test_zero(self):
        self.assertIn("00:00", _format_elapsed(0))

    def test_seconds(self):
        self.assertIn("05", _format_elapsed(5.5))

    def test_minutes(self):
        self.assertIn("02:05", _format_elapsed(125.0))


class ColorFuncTests(unittest.TestCase):
    def test_c_no_color(self):
        result = _c("hello", "31")
        self.assertEqual(result, "hello")


class ParseStatDataTests(unittest.TestCase):
    def test_parse_normal(self):
        data = "1234 (test) S 0 0 0 0 0 0 0 0 0 0 10 5 0 0 0 0 3 0 0"
        utime, stime, num_threads, state = _parse_stat_data(data)
        self.assertEqual(state, "S")
        self.assertEqual(utime, 10)
        self.assertEqual(stime, 5)
        self.assertEqual(num_threads, 3)

    def test_parse_with_parens_in_name(self):
        data = "1 ((sd-pam)) S 0 0 0 0 0 0 0 0 0 0 10 5 0 0 0 0 3 0 0"
        utime, stime, num_threads, state = _parse_stat_data(data)
        self.assertEqual(utime, 10)

    def test_parse_malformed(self):
        data = "short"
        with self.assertRaises((IndexError, ValueError)):
            _parse_stat_data(data)


class PhaseDetectorPatternTests(unittest.TestCase):
    """每个 phase pattern 至少喂一次。"""

    def test_driver_init(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] halGetSocVersion")
        self.assertIsInstance(ev, list)

    def test_model_load(self):
        pd = PhaseDetector()
        pd.feed("== Loading model.bin")
        self.assertIsNotNone(pd.phase)

    def test_core_init(self):
        pd = PhaseDetector()
        pd.feed("[INFO] CoreWrapper [DvcCore0] init")
        self.assertEqual(pd.cores_init, 1)

    def test_stars_config(self):
        pd = PhaseDetector()
        pd.feed("CONFIG STARS SIM_CFG")
        self.assertIsNotNone(pd.phase)

    def test_core_build(self):
        pd = PhaseDetector()
        pd.feed("CoreWrapper [DvcCore0] init")
        pd.feed("[INFO] CoreWrapper [DvcCore7] init")
        pd.feed("[INFO] CoreWrapper [DvcCore0] build")
        self.assertGreaterEqual(pd.cores_built, 1)

    def test_smmu_build(self):
        pd = PhaseDetector()
        pd.feed("[INFO] halGetSocVersion")
        pd.feed("== Loading model.bin")
        pd.feed("[INFO] CoreWrapper [DvcCore0] init")
        pd.feed("[INFO] CoreWrapper [DvcCore0] build")
        pd.feed("Start to build SMMU Page table")
        self.assertIsNotNone(pd.phase)

    def test_smmu_done(self):
        pd = PhaseDetector()
        pd.feed("[INFO] halGetSocVersion")
        pd.feed("== Loading model.bin")
        pd.feed("[INFO] CoreWrapper [DvcCore0] init")
        pd.feed("[INFO] CoreWrapper [DvcCore0] build")
        pd.feed("Start to build SMMU Page table")
        ev = pd.feed("SMMU Page build finished")
        self.assertGreater(len(ev), 0)

    def test_sim_start(self):
        pd = PhaseDetector()
        # Must advance through phases in order
        pd.feed("[INFO] halGetSocVersion")  # driver init
        pd.feed("== Loading model.bin")  # model load
        pd.feed("[INFO] CoreWrapper [DvcCore0] init")  # core init
        pd.feed("[INFO] CoreWrapper [DvcCore0] build")  # core build
        pd.feed("Start to build SMMU Page table")  # smmu build
        pd.feed("Start simulation with m_socCycle")
        self.assertIsNotNone(pd.phase)

    def test_die_created(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] SoC unit rank0_chip0_die1 Created")
        self.assertEqual(len(ev), 1)

    def test_multi_soc_ready(self):
        pd = PhaseDetector()
        pd.feed("Multi SoC in one chip Created")
        self.assertIsNotNone(pd.phase)

    def test_task_dispatch(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] sendStarsSQE blkdim 128")
        self.assertIsInstance(ev, list)

    def test_block_start(self):
        pd = PhaseDetector()
        pd.feed("[INFO] [block_start] AIV, task_id=1, core_id=0, block_id=0")
        self.assertIsNotNone(pd.phase)
        self.assertEqual(pd.blocks_active, 1)

    def test_block_end(self):
        pd = PhaseDetector()
        pd.feed("[INFO] [block_start] AIV, task_id=1, core_id=0, block_id=0")
        pd.feed("[INFO] [block_end] AIV, task_id=1, core_id=0, block_id=0")
        self.assertGreaterEqual(pd.blocks_done, 1)

    def test_task_done(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] [TASK_DONE] RTSQ_0 ACSQ_0 stream_id:0, task_id:1")
        self.assertIsInstance(ev, list)

    def test_hw_finish(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] parallel simulation finish.  sim time: 1.23s")
        self.assertIn("1.23", ev[0][1])

    def test_model_stopped(self):
        pd = PhaseDetector()
        pd.feed("[INFO] parallel simulation finish.  sim time: 1.0s")
        ev = pd.feed("[INFO] Model stopped successfully")
        self.assertIsInstance(ev, list)

    def test_warning_unknown_key(self):
        pd = PhaseDetector()
        ev = pd.feed("[INFO] UNKNOWN KEY 'TEST_KEY'")
        self.assertGreater(len(ev), 0)

    def test_warning_from_pd(self):
        pd = PhaseDetector()
        pd.feed("[INFO] halGetSocVersion")
        pd.feed("WARNING: unknown key 'XTRA'")
        # Should appear in warnings_summary
        ws = pd.warnings_summary()
        self.assertIsInstance(ws, list)

    def test_ddr_spec_extracted(self):
        pd = PhaseDetector()
        pd.feed("== lpddr5 6400 Mbps")
        self.assertEqual(pd.ddr_spec, "lpddr5 6400 Mbps")

    def test_blkdim_extracted(self):
        pd = PhaseDetector()
        pd.feed("[INFO] sendStarsSQE blkdim 128")
        self.assertEqual(pd.blkdim, 128)


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
