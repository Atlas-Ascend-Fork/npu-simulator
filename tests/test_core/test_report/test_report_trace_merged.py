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

"""Unit tests for the ``-m / --merge-trace`` feature of ``cannsim report``.

Covers:

- :func:`merge_chip` / :func:`merge_all` output structure and pid format
- :func:`discover_chip_tasks` multi-chip and single-chip fallback layout
- :func:`_merge_core_traces` producing both products simultaneously
- ``merge_trace`` trigger conditions (requires ``-n``, single vs multi-chip)
- best-effort: merge failure does not break the report pipeline
- CLI ``-m`` argument parsing
"""

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.report.report_trace_merged import (
    discover_chip_tasks,
    merge_all,
    merge_chip,
    _extract_chip_index,
    _extract_kernel_index,
)

report_mod = importlib.import_module("npusim.core.report.report")
main_mod = importlib.import_module("npusim.main")


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _make_core_trace(families):
    """Build a minimal per-core trace with process_name metadata per family.

    Includes one X event, one flow (s/f) pair, and ``_SLOT00`` suffixes on
    tid / task_id so slot-stripping can be verified.
    """
    events = []
    for i, fam in enumerate(families):
        events.append(
            {
                "ph": "M",
                "name": "process_name",
                "pid": i,
                "args": {"name": f"{fam}_{i}_SCALAR"},
            }
        )
        events.append(
            {
                "ph": "X",
                "pid": i,
                "tid": f"T{i}_SLOT00",
                "name": "op",
                "ts": i * 10.0,
                "dur": 5.0,
                "args": {"task_id": f"tk{i}_SLOT00"},
            }
        )
    # flow pair within pid 0
    events.append({"ph": "s", "name": "flow", "pid": 0, "tid": "T0", "id": 42, "ts": 1.0, "cat": "f"})
    events.append({"ph": "f", "name": "flow", "pid": 0, "tid": "T0", "id": 42, "ts": 9.0, "cat": "f"})
    return {"displayTimeUnit": "ns", "traceEvents": events}


def _pnames(data):
    """Extract process_name args.name values from a merged trace dict."""
    return [e["args"]["name"] for e in data["traceEvents"] if e.get("ph") == "M" and e.get("name") == "process_name"]


def _sort_indices(data):
    """Map pid -> sort_index from process_sort_index metadata events."""
    return {
        e["pid"]: e["args"]["sort_index"]
        for e in data["traceEvents"]
        if e.get("ph") == "M" and e.get("name") == "process_sort_index"
    }


def _build_multi_chip_layout(root, chips=(0, 1), kernels=(0, 1), cores=(0, 1), families=None):
    """Create a multi-chip / multi-kernel / multi-core directory layout."""
    if families is None:
        families = {0: ["AIC", "AIV0"], 1: ["AIC", "AIV1"]}
    for chip in chips:
        for kern in kernels:
            for core in cores:
                cd = root / f"chip{chip}_result" / "results" / f"kernel_{kern}_reports" / f"core_{core}"
                cd.mkdir(parents=True)
                with open(cd / f"trace_core{core}.json", "w") as f:
                    json.dump(_make_core_trace(families.get(core, ["AIC"])), f)


def _build_single_chip_layout(root, kernels=(0,), cores=(0, 1), families=None):
    """Create a single-card directory layout (no chip*_result wrapper)."""
    if families is None:
        families = {0: ["AIC", "AIV0"], 1: ["AIC", "AIV1"]}
    for kern in kernels:
        for core in cores:
            cd = root / "results" / f"kernel_{kern}_reports" / f"core_{core}"
            cd.mkdir(parents=True)
            with open(cd / f"trace_core{core}.json", "w") as f:
                json.dump(_make_core_trace(families.get(core, ["AIC"])), f)


# ---------------------------------------------------------------------------
# merge_chip
# ---------------------------------------------------------------------------


class MergeChipTests(unittest.TestCase):
    def test_output_filename_uses_chip_kernel_indices(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(1,), cores=(0,))
            out = str(root / "chip0_result" / "trace_chip0_kernel1.json")
            stats = merge_chip(str(root / "chip0_result"), "kernel_1_reports", out)
            self.assertTrue(Path(out).exists())
            self.assertEqual(stats["num_cores"], 1)

    def test_pid_format_is_core_family(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(0,), cores=(0, 1))
            out = str(root / "out.json")
            merge_chip(str(root / "chip0_result"), "kernel_0_reports", out)
            data = json.load(open(out))
            for name in _pnames(data):
                self.assertRegex(name, r"^CORE\d+\.(AIC|AIV0|AIV1)$", f"bad pid: {name}")

    def test_strips_slot_suffix_from_tid_and_task_id(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(0,), cores=(0,))
            out = str(root / "out.json")
            merge_chip(str(root / "chip0_result"), "kernel_0_reports", out)
            data = json.load(open(out))
            tids = {e.get("tid") for e in data["traceEvents"] if e.get("tid")}
            self.assertFalse(any("_SLOT" in t for t in tids))
            for e in data["traceEvents"]:
                args = e.get("args")
                if isinstance(args, dict) and "task_id" in args:
                    self.assertNotIn("_SLOT", args["task_id"])

    def test_sort_index_contiguous_and_ordered_by_core(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(0,), cores=(0, 1, 2))
            out = str(root / "out.json")
            merge_chip(str(root / "chip0_result"), "kernel_0_reports", out)
            data = json.load(open(out))
            si = _sort_indices(data)
            vals = sorted(si.values())
            self.assertEqual(vals, list(range(1, len(vals) + 1)))
            # core 0 pids < core 1 pids < core 2 pids
            for i in range(2):
                ci = max(v for k, v in si.items() if k.startswith(f"CORE{i}."))
                ci1 = min(v for k, v in si.items() if k.startswith(f"CORE{i + 1}."))
                self.assertLess(ci, ci1)

    def test_flow_ids_reencoded_with_pid_prefix(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(0,), cores=(0,))
            out = str(root / "out.json")
            merge_chip(str(root / "chip0_result"), "kernel_0_reports", out)
            data = json.load(open(out))
            flows = [e for e in data["traceEvents"] if e.get("ph") in ("s", "f")]
            self.assertTrue(flows)
            for e in flows:
                self.assertTrue(str(e["id"]).startswith("CORE"))

    def test_output_top_level_fields(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0,), kernels=(0,), cores=(0,))
            out = str(root / "out.json")
            merge_chip(str(root / "chip0_result"), "kernel_0_reports", out)
            data = json.load(open(out))
            self.assertEqual(data["displayTimeUnit"], "ns")
            self.assertEqual(data["profilingType"], "op")
            self.assertEqual(data["schemaVersion"], 1)
            self.assertIn("traceEvents", data)

    def test_missing_reports_dir_raises(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            with self.assertRaises(FileNotFoundError):
                merge_chip(str(tmp), "kernel_0_reports", str(Path(tmp) / "x.json"))


# ---------------------------------------------------------------------------
# merge_all
# ---------------------------------------------------------------------------


class MergeAllTests(unittest.TestCase):
    def test_pid_format_is_chip_kernel_core_family(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root)
            out = str(root / "trace_allchips.json")
            merge_all(str(root), out)
            data = json.load(open(out))
            for name in _pnames(data):
                self.assertRegex(
                    name,
                    r"^CHIP\d+\.KERNEL\d+\.CORE\d+\.(AIC|AIV0|AIV1)$",
                    f"bad pid: {name}",
                )

    def test_includes_all_chips_and_kernels(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0, 1), kernels=(0, 1))
            out = str(root / "trace_allchips.json")
            stats = merge_all(str(root), out)
            data = json.load(open(out))
            chips = {p.split(".")[0] for p in _pnames(data)}
            kerns = {p.split(".")[1] for p in _pnames(data)}
            self.assertEqual(chips, {"CHIP0", "CHIP1"})
            self.assertEqual(kerns, {"KERNEL0", "KERNEL1"})
            self.assertEqual(stats["num_tasks"], 4)

    def test_sort_index_chip0_before_chip1(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0, 1), kernels=(0,))
            out = str(root / "trace_allchips.json")
            merge_all(str(root), out)
            data = json.load(open(out))
            si = _sort_indices(data)
            c0_max = max(v for k, v in si.items() if k.startswith("CHIP0."))
            c1_min = min(v for k, v in si.items() if k.startswith("CHIP1."))
            self.assertLess(c0_max, c1_min)

    def test_event_count_equals_sum_of_merge_chip(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0, 1), kernels=(0, 1))
            out_all = str(root / "trace_allchips.json")
            stats_all = merge_all(str(root), out_all)
            total = 0
            for chip in (0, 1):
                for kern in (0, 1):
                    co = str(root / f"c{chip}k{kern}.json")
                    s = merge_chip(
                        str(root / f"chip{chip}_result"),
                        f"kernel_{kern}_reports",
                        co,
                    )
                    total += s["num_events"]
            self.assertEqual(stats_all["num_events"], total)

    def test_single_chip_fallback_uses_chip0(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_single_chip_layout(root)
            out = str(root / "trace_allchips.json")
            merge_all(str(root), out)
            data = json.load(open(out))
            for name in _pnames(data):
                self.assertTrue(name.startswith("CHIP0.KERNEL0."))

    def test_empty_dir_raises(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            with self.assertRaises(FileNotFoundError):
                merge_all(str(tmp), str(Path(tmp) / "x.json"))


# ---------------------------------------------------------------------------
# discover_chip_tasks
# ---------------------------------------------------------------------------


class DiscoverChipTasksTests(unittest.TestCase):
    def test_multi_chip_finds_all_chip_kernel_pairs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0, 1), kernels=(0, 1))
            tasks = discover_chip_tasks(str(root))
            self.assertEqual(len(tasks), 4)
            out_names = {Path(t[2]).name for t in tasks}
            self.assertEqual(
                out_names,
                {
                    "trace_chip0_kernel0.json",
                    "trace_chip0_kernel1.json",
                    "trace_chip1_kernel0.json",
                    "trace_chip1_kernel1.json",
                },
            )

    def test_single_chip_fallback(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_single_chip_layout(root, kernels=(0,))
            tasks = discover_chip_tasks(str(root))
            self.assertEqual(len(tasks), 1)
            chip_dir, kernel_dir, out_path = tasks[0]
            self.assertEqual(Path(out_path).name, "trace_chip0_kernel0.json")

    def test_empty_dir_returns_empty_list(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            self.assertEqual(discover_chip_tasks(str(tmp)), [])

    def test_extract_chip_index(self):
        self.assertEqual(_extract_chip_index("/x/chip0_result"), 0)
        self.assertEqual(_extract_chip_index("/x/chip12_result"), 12)
        self.assertEqual(_extract_chip_index("/x"), 0)

    def test_extract_kernel_index(self):
        self.assertEqual(_extract_kernel_index("kernel_0_reports"), 0)
        self.assertEqual(_extract_kernel_index("kernel_7_reports"), 7)
        self.assertEqual(_extract_kernel_index("foo_reports"), 0)


# ---------------------------------------------------------------------------
# _merge_core_traces (orchestration)
# ---------------------------------------------------------------------------


class MergeCoreTracesTests(unittest.TestCase):
    def test_produces_both_per_chip_and_global_outputs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_multi_chip_layout(root, chips=(0, 1), kernels=(0,))
            report_mod._merge_core_traces(root)
            for chip in (0, 1):
                self.assertTrue((root / f"chip{chip}_result" / f"trace_chip{chip}_kernel0.json").exists())
            self.assertTrue((root / "trace_allchips.json").exists())

    def test_empty_dir_warns_and_no_crash(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            report_mod._merge_core_traces(Path(tmp))
            self.assertFalse((Path(tmp) / "trace_allchips.json").exists())

    def test_single_chip_produces_both_outputs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            _build_single_chip_layout(root)
            report_mod._merge_core_traces(root)
            self.assertTrue((root / "trace_chip0_kernel0.json").exists())
            self.assertTrue((root / "trace_allchips.json").exists())


# ---------------------------------------------------------------------------
# merge_trace trigger conditions in generate_report / report
# ---------------------------------------------------------------------------


class _BiProfSuccessChain:
    """Stub chain that reports success without running BiProfRunner."""

    def execute(self, biprof_args, export_abs, output_abs, core_ids, **_kwargs):
        return ("BiProfRunner module", True)


class MergeTraceTriggerTests(unittest.TestCase):
    def _make_export_with_trace(self, tmp):
        """Create a single-card export dir with a pre-placed trace file.

        The trace is placed in the post-BiProfRunner layout
        (``output/results/kernel_0_reports/core_0/trace_core0.json``) so
        that ``_merge_core_traces`` can discover it.  The stubbed
        BiProfRunner chain skips actual report generation, so we seed the
        expected output structure directly.
        """
        export_dir = Path(tmp) / "export"
        output_dir = Path(tmp) / "report"
        (export_dir).mkdir()
        (export_dir / "instr.bin").write_bytes(b"")
        core_dir = output_dir / "results" / "kernel_0_reports" / "core_0"
        core_dir.mkdir(parents=True)
        (core_dir / "trace_core0.json").write_text(json.dumps(_make_core_trace(["AIC"])), encoding="utf-8")
        return export_dir, output_dir

    def test_single_chip_merge_trace_with_core_id_triggers_merge(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            export_dir, output_dir = self._make_export_with_trace(tmp)
            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                    merge_trace=True,
                )
            self.assertTrue(ok)
            self.assertTrue((output_dir / "trace_allchips.json").exists())
            self.assertTrue((output_dir / "trace_chip0_kernel0.json").exists())

    def test_merge_trace_without_core_id_does_not_merge(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            export_dir, output_dir = self._make_export_with_trace(tmp)
            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id=None,
                    merge_trace=True,
                )
            self.assertTrue(ok)
            self.assertFalse((output_dir / "trace_allchips.json").exists())

    def test_no_merge_trace_does_not_merge(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            export_dir, output_dir = self._make_export_with_trace(tmp)
            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.generate_report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                    merge_trace=False,
                )
            self.assertTrue(ok)
            self.assertFalse((output_dir / "trace_allchips.json").exists())

    def test_multi_chip_merge_trace_triggers_global_merge(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")
            # pre-place traces in the post-BiProfRunner layout
            for chip in (0, 1):
                cd = output_dir / f"chip{chip}_result" / "results" / "kernel_0_reports" / "core_0"
                cd.mkdir(parents=True)
                (cd / "trace_core0.json").write_text(json.dumps(_make_core_trace(["AIC"])), encoding="utf-8")

            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id="0",
                    merge_trace=True,
                )
            self.assertTrue(ok)
            self.assertTrue((output_dir / "trace_allchips.json").exists())
            for chip in (0, 1):
                self.assertTrue((output_dir / f"chip{chip}_result" / f"trace_chip{chip}_kernel0.json").exists())

    def test_multi_chip_merge_trace_without_core_id_no_merge(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            export_dir = root / "export"
            output_dir = root / "report"
            export_dir.mkdir()
            (export_dir / "chip0_instr.bin").write_bytes(b"")
            (export_dir / "chip1_instr.bin").write_bytes(b"")
            with patch.object(report_mod, "_REPORT_CHAIN", _BiProfSuccessChain()):
                ok = report_mod.report(
                    export=str(export_dir),
                    output=str(output_dir),
                    core_id=None,
                    merge_trace=True,
                )
            self.assertTrue(ok)
            self.assertFalse((output_dir / "trace_allchips.json").exists())


# ---------------------------------------------------------------------------
# CLI -m argument parsing
# ---------------------------------------------------------------------------


class CLIArgumentTests(unittest.TestCase):
    def test_m_flag_sets_merge_trace_true(self):
        parser = main_mod.setup_parser()
        args = parser.parse_args(["report", "-e", "./out", "-n", "all", "-m"])
        self.assertTrue(args.merge_trace)

    def test_long_flag_sets_merge_trace_true(self):
        parser = main_mod.setup_parser()
        args = parser.parse_args(["report", "-e", "./out", "-n", "all", "--merge-trace"])
        self.assertTrue(args.merge_trace)

    def test_default_merge_trace_false(self):
        parser = main_mod.setup_parser()
        args = parser.parse_args(["report", "-e", "./out", "-n", "all"])
        self.assertFalse(args.merge_trace)

    def test_merge_trace_forwarded_to_report_function(self):
        """Verify handle_report_command passes merge_trace through."""
        with patch.object(main_mod, "report", return_value=True) as mock_report:
            parser = main_mod.setup_parser()
            args = parser.parse_args(["report", "-e", "./out", "-n", "all", "-m"])
            main_mod.handle_report_command(args)
            _args, kwargs = mock_report.call_args
            self.assertTrue(kwargs.get("merge_trace"))


if __name__ == "__main__":
    unittest.main()
