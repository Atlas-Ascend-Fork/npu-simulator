#!/usr/bin/env python3
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
"""Run with python3 -m unittest tests.test_core.test_prof_demo -v."""

import ast
import collections
import dataclasses
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from npusim.prof_demo.cli import analyze, generate_reports, main
from npusim.prof_demo.config import config, ReportConfig
from npusim.prof_demo.database import database_handler
from npusim.prof_demo.reports import tracing


MODULE = Path(__file__).resolve().parents[2] / "npusim" / "prof_demo"


def make_db(path, name="synthetic", launch=0):
    """Small two-core fixture with SIMD, DMA, CUBE/FIXP and flag/buffer flows."""
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE KernelInfo(KernelInfoAttr TEXT PRIMARY KEY, KernelInfoVal);
        CREATE TABLE ExecutedInstructions(
            ExecInstrId INTEGER PRIMARY KEY, SourceInstrAddr INTEGER,
            ExecInstrTickStart INTEGER, ExecInstrTickEnd INTEGER,
            ExecInstrName TEXT, ExecInstrParams TEXT, InstrTypeId INTEGER,
            ExecInstrCoreId INTEGER, CoreTypeId INTEGER, ExtendParams TEXT,
            SourceInstrEncoding INTEGER, ExecInstrIsWait INTEGER,
            ExecInstrIsSynchronization INTEGER, ExecInstrIsDurationExcluded INTEGER,
            ExecInstrVfClass TEXT, ExecInstrVfSimtClass TEXT
        );
    """)
    rows = []
    types = {
        1: "SCALAR",
        2: "CUBE",
        3: "MTE1",
        4: "MTE2",
        5: "MTE3",
        7: "FIXP",
        50: "RVECEX",
        51: "RVECLP",
        52: "RVECSU",
        53: "RVECST",
        54: "RVECLD",
        55: "PUSHQ",
    }

    def add(name, start, end, pipe=1, core=0, core_type=2, **facts):
        index = len(rows) + 1
        params = dict(
            instr_type=types[pipe],
            core_type={1: "AIC", 2: "AIV0", 3: "AIV1"}[core_type],
            queue_type="SCALAR",
            is_wait=False,
            is_sync=False,
            is_duration_excluded=False,
            vf_class=None,
            vf_simt_class=None,
        )
        params.update(facts)
        rows.append(
            (
                index | (core << 32) | (core_type << 38),
                index * 4,
                start,
                end,
                name,
                "RAW_NOT_FOR_REPORT",
                pipe,
                core,
                core_type,
                json.dumps(params),
                0xDEADBEEF,
                params["is_wait"],
                params["is_sync"],
                params["is_duration_excluded"],
                params["vf_class"],
                params["vf_simt_class"],
            )
        )

    for core, core_type in ((0, 2), (1, 1)):
        add("LD", 1, 3, core=core, core_type=core_type)
        add("ADD", 4, 5, core=core, core_type=core_type)
        add("ST", 6, 8, core=core, core_type=core_type)
    add("VF", 10, 100, 55)
    add("RV_VADD", 15, 20, 50, vf_class="EXECUTION")
    add("RV_VLD", 21, 25, 54, vf_class="LOAD_STORE", processed_bytes=128, processed_elements=64)
    add("RV_VST", 26, 30, 53, vf_class="LOAD_STORE", processed_bytes=128)
    add("RV_VADDS", 31, 35, 52, vf_class="SCALAR")
    add("RV_VMAX", 36, 40, 50, vf_class="EXECUTION")
    add(
        "MOV_SRC_TO_DST_ALIGNv2",
        40,
        50,
        4,
        processed_bytes=256,
        src_mem="OUT",
        dst_mem="UB",
        src_addr=4096,
        burst_number=1,
        burst_len=256,
    )
    add(
        "MOV_SRC_TO_DST_ALIGNv2",
        50,
        60,
        4,
        core=1,
        core_type=1,
        processed_bytes=512,
        src_mem="OUT",
        dst_mem="L1",
        src_addr=8192,
    )
    add("MMAD", 61, 75, 2, core=1, core_type=1, m=16, n=16, k=16, matrix_a_dtype="f16", matrix_a_processed_bytes=512)
    add(
        "FIX_L0C_TO_DST",
        76,
        85,
        7,
        core=1,
        core_type=1,
        m=16,
        n=16,
        src_mem="L0C",
        dst_mem="UB",
        dst_ub_mask=3,
        processed_bytes=512,
    )
    add("SET_FLAG", 86, 87, is_sync=True, sync_kind="SET_FLAG", target_pipe="MTE2", consumer_pipe="VEC", flag_id=0)
    add(
        "WAIT_FLAG",
        88,
        90,
        is_sync=True,
        is_wait=True,
        sync_kind="WAIT_FLAG",
        target_pipe="MTE2",
        consumer_pipe="VEC",
        flag_id=0,
    )
    add(
        "RLS_BUF", 91, 92, is_sync=True, sync_kind="RELEASE_BUFFER", target_pipe="MTE2", buf_id=0, buf_mode=0, buf_cnt=0
    )
    add(
        "GET_BUF",
        93,
        95,
        is_sync=True,
        is_wait=True,
        sync_kind="ACQUIRE_BUFFER",
        target_pipe="MTE2",
        buf_id=0,
        buf_mode=0,
        buf_cnt=1,
    )
    metadata = {
        "arch": "v100",
        "contract_version": 7,
        "frequency_ghz": 1.65,
        "exec_time_ticks": 99,
        "ai_core_active": 2,
        "instrs_exec_num": len(rows),
        "name": name,
        "launch_id": launch,
    }
    connection.executemany("INSERT INTO KernelInfo VALUES (?, ?)", metadata.items())
    connection.executemany("INSERT INTO ExecutedInstructions VALUES (" + ",".join("?" * 16) + ")", rows)
    connection.commit()
    connection.close()


# prof_demo/npusim.prof: core_<N>_tracing_report_<k>.json[.gz] in the report root.
# cannsim report: renamed to core_<N>/trace_core<N>.json[.gz] (report_path.py).
TRACE_NAME_RE = re.compile(r"^(?:core_(\d+)_tracing_report_\d+|trace_core(\d+))\.json(?:\.gz)?$")


def normalized_trace(directory):
    """Compare effective events, ignoring metadata-only tracks and PID numbering."""
    events = []
    for path in sorted(Path(directory).rglob("*.json*")):
        match = TRACE_NAME_RE.match(path.name)
        if not match:
            continue
        core = match[1] or match[2]
        if path.suffix == ".gz":
            with gzip.open(path, "rt") as stream:
                trace = json.load(stream)["traceEvents"]
        else:
            trace = json.loads(path.read_text())["traceEvents"]
        names = {event["pid"]: event["args"]["name"] for event in trace if event["name"] == "process_name"}
        for event in trace:
            if "ts" not in event:
                continue
            event["pid"] = names[event["pid"]]
            if "unit" in event.get("args", {}):
                event["args"]["unit"] = event["pid"]
            events.append(json.dumps([core, event], sort_keys=True))
    return collections.Counter(events)


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "input.db"
        make_db(self.db)
        for field in dataclasses.fields(config):
            setattr(config, field.name, getattr(ReportConfig(), field.name))
        self.addCleanup(database_handler.disconnect)

    def report(self):
        output = self.root / "reports"
        generate_reports(self.db, output)
        return next(output.iterdir())

    def test_reports_metrics_and_source_unchanged(self):
        digest = hashlib.sha256(self.db.read_bytes()).hexdigest()
        report = self.report()
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), digest)
        self.assertEqual(
            {p.name for p in report.iterdir()},
            {"summary.json", "aicore_utilization.json", "core_0_tracing_report_0.json", "core_1_tracing_report_0.json"},
        )
        summary = json.loads((report / "summary.json").read_text())
        self.assertGreater(summary["aiv_vector_instructions"]["SIMD_ExecIPC"], 0)
        self.assertEqual(summary["simd_vf_metrics"]["vf_total_executions"], 1)
        self.assertIn("AIC_MTE2_OUT_TO_L1", summary["bandwidth"])
        self.assertEqual(summary["cache"], {"icache_refill_count": 0, "icache_refill_ticks": None})
        trace = json.loads((report / "core_0_tracing_report_0.json").read_text())["traceEvents"]
        self.assertEqual(sum(e["ph"] in ("s", "f") for e in trace), 4)
        args = next(e["args"] for e in trace if e["name"] == "RV_VLD")
        self.assertEqual(args["vector_utilization"], "50%")
        self.assertNotIn("RAW_NOT_FOR_REPORT", (report / "core_0_tracing_report_0.json").read_text())
        self.assertNotIn("encoding", args)
        occupancy = json.loads((report / "aicore_utilization.json").read_text())
        self.assertEqual(len(occupancy["traceEvents"]), 2)

    def test_vf_report_json_switch(self):
        self.assertFalse(list(self.report().glob("*_vf_report.json")))
        config.vf_report_json = True
        output = self.root / "vf"
        generate_reports(self.db, output)
        report = next(output.iterdir())
        self.assertEqual(
            {p.name for p in report.iterdir()},
            {
                "summary.json",
                "aicore_utilization.json",
                "core_0_tracing_report_0.json",
                "core_1_tracing_report_0.json",
                "input_vf_report.json",
            },
        )
        vf = json.loads((report / "input_vf_report.json").read_text())
        self.assertEqual(vf["version"], "1.2")
        self.assertEqual(set(vf), {"version", "kernel", "vfs", "timeline"})
        self.assertEqual(vf["kernel"]["name"], "synthetic")
        self.assertEqual(vf["kernel"]["scalar_ipc_overall"], "0.09")
        (entry,) = vf["vfs"]
        self.assertEqual((entry["id"], entry["type"], entry["executions"]), (0, "simd", 1))
        self.assertEqual(entry["ipc"], {"scalar": 0.04, "exec": 0.08, "ldst": 0.08, "total": 0.2})
        self.assertEqual(entry["instr_profile"]["total_count"], 5)
        self.assertEqual([e["name"] for e in vf["timeline"]], ["RV_VADD", "RV_VLD", "RV_VST", "RV_VADDS", "RV_VMAX"])

    def test_vf_report_json_without_vf(self):
        config.vf_report_json = True
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM ExecutedInstructions WHERE ExecInstrName='VF'")
        self.assertFalse(list(self.report().glob("*_vf_report.json")))

    def test_vf_report_scalar_ipc_zero_span_core(self):
        # Legacy scalar_ipc_analyzer divided by zero here and left the metric absent.
        config.vf_report_json = True
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE ExecutedInstructions SET ExecInstrTickStart=1 WHERE ExecInstrCoreId=1")
        vf = json.loads((self.report() / "input_vf_report.json").read_text())
        self.assertIsNone(vf["kernel"]["scalar_ipc_overall"])
        config.core_id = "0"
        generate_reports(self.db, self.root / "core0")
        vf = json.loads(next((self.root / "core0").glob("*/input_vf_report.json")).read_text())
        self.assertEqual(vf["kernel"]["scalar_ipc_overall"], "0.1")

        database_handler.connect(self.db)
        self.assertEqual(database_handler.execute("PRAGMA query_only").fetchone()[0], 0)
        with self.assertRaises(sqlite3.OperationalError):
            database_handler.execute("UPDATE sim.KernelInfo SET KernelInfoVal=0")

    def test_source_and_analysis_read_only_guards(self):
        analyze(self.db)
        for sql in ("UPDATE sim.KernelInfo SET KernelInfoVal=0", "DELETE FROM main.PipesUtilization"):
            with self.assertRaises(sqlite3.OperationalError):
                database_handler.execute(sql)

    def test_selected_core(self):
        config.core_id = "1"
        report = self.report()
        self.assertFalse((report / "core_0_tracing_report_0.json").exists())
        self.assertTrue((report / "core_1_tracing_report_0.json").exists())
        # Legacy occupancy and several summary metrics remain kernel-wide.
        occupancy = json.loads((report / "aicore_utilization.json").read_text())
        self.assertEqual(len(occupancy["traceEvents"]), 2)

    def test_sharding_and_gzip_preserve_events(self):
        reference = normalized_trace(self.report())
        config.json_max_size = 900
        with patch.object(tracing, "GZIP_THRESHOLD", 100):
            output = self.root / "compressed"
            generate_reports(self.db, output)
        report = next(output.iterdir())
        self.assertGreater(len(list(report.glob("*.gz"))), 2)
        self.assertEqual(normalized_trace(report), reference)
        self.assertEqual(len(tracing.divide_json([{"ts": 1, "name": "x" * 100}], 1)), 1)

    def test_normalized_trace_accepts_cannsim_report_layout(self):
        report = self.report()
        renamed = self.root / "renamed"
        for core in (0, 1):
            (renamed / f"core_{core}").mkdir(parents=True)
            shutil.copyfile(
                report / f"core_{core}_tracing_report_0.json", renamed / f"core_{core}" / f"trace_core{core}.json"
            )
        self.assertTrue(normalized_trace(report))
        self.assertEqual(normalized_trace(renamed), normalized_trace(report))

    def test_real_gzip_threshold(self):
        config.json_max_size = -1
        output = self.root / "gzip"
        output.mkdir()
        event = {"name": "x" * tracing.GZIP_THRESHOLD, "pid": 0, "tid": "test", "ph": "X", "ts": 0, "dur": 1}
        with patch.object(tracing.BackendDBManager, "get_active_cores", return_value=[0]), patch.object(
            tracing, "core_events", return_value=[event]
        ):
            tracing.generate(output)
        self.assertEqual([p.name for p in output.iterdir()], ["core_0_tracing_report_0.json.gz"])
        with gzip.open(output / "core_0_tracing_report_0.json.gz", "rt") as stream:
            self.assertEqual(json.load(stream)["traceEvents"], [event])

    def test_ticks_mode(self):
        config.no_ticks_to_ns = True
        report = self.report()
        events = json.loads((report / "core_0_tracing_report_0.json").read_text())["traceEvents"]
        load = next(event for event in events if event["name"] == "LD")
        self.assertEqual(load["ts"], 0.001)
        self.assertEqual(load["dur"], 0.002)

    def test_invalid_metadata(self):
        for attr, value in (
            ("arch", "unknown"),
            ("contract_version", 99),
            ("frequency_ghz", 0),
            ("exec_time_ticks", 0),
        ):
            with self.subTest(attr=attr):
                with sqlite3.connect(self.db) as connection:
                    previous = connection.execute(
                        "SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr=?", (attr,)
                    ).fetchone()[0]
                    connection.execute("UPDATE KernelInfo SET KernelInfoVal=? WHERE KernelInfoAttr=?", (value, attr))
                with self.assertRaises(ValueError):
                    self.report()
                self.assertIsNone(database_handler.connection)
                with sqlite3.connect(self.db) as connection:
                    connection.execute("UPDATE KernelInfo SET KernelInfoVal=? WHERE KernelInfoAttr=?", (previous, attr))

    def test_no_scalar_instructions(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM ExecutedInstructions WHERE InstrTypeId=1")
        summary = json.loads((self.report() / "summary.json").read_text())
        self.assertEqual(summary["scalar_instructions"]["AIV0"]["load_store_ratio"], 0)

    def test_empty_input_directory(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        with self.assertRaisesRegex(ValueError, "No .db files"):
            generate_reports(inputs, self.root / "reports")

    def test_window_one_and_zero_duration_scalar(self):
        config.sliding_window = config.sliding_window_vec = 1
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                "UPDATE ExecutedInstructions SET ExecInstrTickEnd=ExecInstrTickStart WHERE ExecInstrName='ADD'"
            )
        self.report()

    def test_output_conflict_is_not_deleted(self):
        output = self.root / "reports"
        output.mkdir()
        sentinel = output / "keep"
        sentinel.write_text("unchanged")
        with self.assertRaises(FileExistsError):
            generate_reports(self.db, output)
        self.assertEqual(sentinel.read_text(), "unchanged")

    def test_bad_facts_fail_without_published_output(self):
        for value in (None, "not json", "[]", "{}"):
            with self.subTest(value=value):
                with sqlite3.connect(self.db) as connection:
                    connection.execute("UPDATE ExecutedInstructions SET ExtendParams=?", (value,))
                with self.assertRaises((ValueError, RuntimeError)):
                    generate_reports(self.db, self.root / "reports")
                self.assertFalse((self.root / "reports").exists())
                self.assertFalse(list(self.root.glob(".prof-demo-*")))

    def test_invalid_core(self):
        for spec in ("99", "2-1", "-1", "abc", ""):
            config.core_id = spec
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                generate_reports(self.db, self.root / "reports")

    def test_empty_instructions(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("DELETE FROM ExecutedInstructions")
        with self.assertRaisesRegex(ValueError, "No executed instructions"):
            self.report()

    def test_zero_span(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("UPDATE ExecutedInstructions SET ExecInstrTickStart=0, ExecInstrTickEnd=0")
        with self.assertRaisesRegex(ValueError, "span must be positive"):
            self.report()

    def test_directory_input_and_no_cached_facts(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        shutil.copyfile(self.db, inputs / "first.db")
        make_db(inputs / "second.db", name="second", launch=1)
        config.vf_report_json = True
        generate_reports(inputs, self.root / "batch")
        self.assertEqual(len(list((self.root / "batch").iterdir())), 2)
        self.assertEqual(
            sorted(p.name for p in (self.root / "batch").glob("*/*_vf_report.json")),
            ["first_vf_report.json", "second_vf_report.json"],
        )
        config.vf_report_json = False
        # Repeated sessions against the same path must re-read facts.
        with sqlite3.connect(self.db) as connection:
            rows = connection.execute(
                "SELECT ExecInstrId, ExtendParams FROM ExecutedInstructions WHERE ExecInstrName='RV_VLD'"
            ).fetchall()
            for exec_id, raw in rows:
                facts = json.loads(raw)
                facts["processed_bytes"] = 64
                connection.execute(
                    "UPDATE ExecutedInstructions SET ExtendParams=? WHERE ExecInstrId=?",
                    (json.dumps(facts), exec_id),
                )
        trace = json.loads((self.report() / "core_0_tracing_report_0.json").read_text())["traceEvents"]
        self.assertEqual(next(e["args"]["vector_utilization"] for e in trace if e["name"] == "RV_VLD"), "25%")

    def test_duplicate_report_names_fail(self):
        inputs = self.root / "inputs"
        inputs.mkdir()
        shutil.copyfile(self.db, inputs / "a.db")
        shutil.copyfile(self.db, inputs / "b.db")
        with self.assertRaises(FileExistsError):
            generate_reports(inputs, self.root / "reports")
        self.assertFalse((self.root / "reports").exists())

    def test_without_sqlite_json_extension(self):
        connect = database_handler.connect

        def connect_without_json(path):
            connect(path)

            def authorize(action, arg1, arg2, database, trigger):
                if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower().startswith("json"):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            database_handler.connection.set_authorizer(authorize)

        with patch.object(database_handler, "connect", side_effect=connect_without_json):
            self.report()

    def test_python37_syntax(self):
        # Python 3.7 uses its own grammar; newer interpreters emulate it.
        kwargs = {"feature_version": 7} if sys.version_info >= (3, 8) else {}
        for path in sorted(MODULE.rglob("*.py")):
            with self.subTest(path=path.relative_to(MODULE)):
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), **kwargs)

    def test_without_site_packages_or_cannsim(self):
        # Copy only the report submodule; -S prevents loading any site-packages.
        isolated = self.root / "isolated"
        package = isolated / "npusim"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        shutil.copytree(MODULE, package / "prof_demo", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        result = subprocess.run(
            [sys.executable, "-S", "-m", "npusim.prof_demo", "--input-db", str(self.db), "-o", str(self.root / "standalone")],
            cwd=isolated,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": ""},
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_failure(self):
        self.assertEqual(main(["--input-db", str(self.root / "missing.db"), "-o", str(self.root / "reports")]), 1)


@unittest.skipUnless(
    os.getenv("PROF_DEMO_SAMPLE_DB") and os.getenv("PROF_DEMO_BASELINE_DIR"), "optional external sample/baseline"
)
class SampleParityTest(unittest.TestCase):
    def test_original_reports(self):
        source = Path(os.environ["PROF_DEMO_SAMPLE_DB"])
        baseline = Path(os.environ["PROF_DEMO_BASELINE_DIR"])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with TemporaryDirectory() as scratch:
            output = Path(scratch) / "reports"
            self.assertEqual(main(["--input-db", str(source), "-o", str(output)]), 0)
            report = next(output.iterdir())
            for filename in ("summary.json", "aicore_utilization.json"):
                self.assertEqual(
                    json.loads((baseline / filename).read_text()), json.loads((report / filename).read_text())
                )
            expected = normalized_trace(baseline)
            self.assertTrue(
                expected,
                f"No trace events found under baseline {baseline}; expected "
                "core_<N>_tracing_report_<k>.json[.gz] or core_<N>/trace_core<N>.json[.gz]",
            )
            self.assertEqual(normalized_trace(report), expected)
            vf_baselines = sorted(baseline.glob("*_vf_report.json"))
            if vf_baselines:
                vf_output = Path(scratch) / "vf_reports"
                self.assertEqual(main(["--input-db", str(source), "-o", str(vf_output), "--vf-report-json"]), 0)
                vf_report = next(vf_output.iterdir())
                produced = sorted(vf_report.glob("*_vf_report.json"))
                self.assertEqual([p.name for p in produced], [p.name for p in vf_baselines])
                for expected_path, produced_path in zip(vf_baselines, produced):
                    self.assertEqual(json.loads(expected_path.read_text()), json.loads(produced_path.read_text()))
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
