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
"""A sequential JSON-only pipeline. No toolkit, collector or legacy imports."""

import argparse
import dataclasses
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

from npusim.prof_demo.config import config
from npusim.prof_demo.database import database_handler, BackendDBManager
from npusim.prof_demo.extend_params import install_extend_params_projection
from npusim.prof_demo.analysis.pipes import PipeUtilizationAnalyzer
from npusim.prof_demo.analysis.dma import DmaAnalyzer
from npusim.prof_demo.analysis.dependencies import PipeDependencyAnalyzer
from npusim.prof_demo.analysis.vf import Ascend950VFIPCAnalyzer
from npusim.prof_demo.analysis.simt import Ascend950VFSimtIPCAnalyzer
from npusim.prof_demo.analysis.scalar import Ascend950ScalarInstructionPerCycleDynamicAnalyzer
from npusim.prof_demo.analysis.unit import UnitUtilizationAnalyzer
from npusim.prof_demo.reports import tracing
from npusim.prof_demo.reports.occupancy import DatabaseManager as OccupancyManager
from npusim.prof_demo.reports.summary import SummaryReporter
from npusim.prof_demo.reports.vf_report import build_vf_report


ANALYZERS = (
    PipeUtilizationAnalyzer,
    DmaAnalyzer,
    PipeDependencyAnalyzer,
    Ascend950VFIPCAnalyzer,
    Ascend950VFSimtIPCAnalyzer,
    Ascend950ScalarInstructionPerCycleDynamicAnalyzer,
    UnitUtilizationAnalyzer,
)


def analyze(path):
    """Caller owns disconnect in finally, including initialization failures."""
    database_handler.connect(path)
    BackendDBManager.get_active_cores()  # Fail before doing costly analysis.
    install_extend_params_projection()
    for analyzer_type in ANALYZERS:
        analyzer = analyzer_type()
        analyzer.process()
    database_handler.enable_query_only()


def report_name(index):
    metadata = dict(database_handler.execute("SELECT * FROM sim.KernelInfo"))
    name = str(metadata.get("name") or "kernel").strip()
    component = re.sub(r"[^\w.\-+=:@]+", "_", name)[:200] or "kernel"
    try:
        launch = int(metadata.get("launch_id", index))
    except (ValueError, TypeError):
        launch = index
    return f"kernel_{launch:04d}_{component}_reports"


def generate_reports(input_db, output):
    """Generate all reports before publishing; never overwrite existing output."""
    source, output = Path(input_db).resolve(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists; choose a new directory: {output}")
    if source.is_dir():
        inputs = sorted(path for path in source.glob("*.db") if path.is_file())
    elif source.is_file():
        inputs = [source]
    else:
        raise ValueError(f"Input does not exist: {source}")
    if not inputs:
        raise ValueError(f"No .db files found in {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".prof-demo-", dir=output.parent) as scratch:
        for index, path in enumerate(inputs):
            try:
                analyze(path)
                report_dir = Path(scratch) / report_name(index)
                report_dir.mkdir()  # Duplicate kernel launch/name is an error, not an overwrite.
                tracing.generate(report_dir)
                occupancy = [dataclasses.asdict(event) for event in OccupancyManager.get_occupancy_events()]
                reports = {
                    "aicore_utilization.json": {"traceEvents": occupancy, "displayTimeUnit": "ns"},
                    "summary.json": SummaryReporter().get_summary(),
                }
                vf_report = build_vf_report() if config.vf_report_json else None
                if vf_report is not None:
                    reports[f"{path.stem}_vf_report.json"] = vf_report
                for name, content in reports.items():
                    with (report_dir / name).open("x", encoding="utf-8") as stream:
                        json.dump(content, stream, allow_nan=False)
            finally:
                database_handler.disconnect()
        output.mkdir()  # Exclusive creation also detects a competing writer.
        for report_dir in sorted(Path(scratch).iterdir()):
            shutil.move(str(report_dir), str(output / report_dir.name))
    return output


def positive_int(text):
    value = int(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def json_limit(text):
    value = int(text)
    if value != -1 and value <= 0:
        raise argparse.ArgumentTypeError("must be positive or -1 (no splitting)")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate trace, aicore utilization and summary JSON (optionally VF report JSON) from v100 helper DBs"
    )
    parser.add_argument("--input-db", required=True, type=Path, help="DB file or directory containing *.db")
    parser.add_argument("-o", "--output", required=True, type=Path, help="new output directory (never overwritten)")
    parser.add_argument("--core-id", default="all", help="trace/pipe/scalar/unit core selection: all, 0, or 0-2,5")
    parser.add_argument("--no-ticks-to-ns", action="store_true", help="legacy mode: treat one tick as one ns")
    parser.add_argument(
        "--json-max-size", type=json_limit, default=524288000, help="trace shard payload target; -1 disables splitting"
    )
    parser.add_argument("--sliding-window", type=positive_int, default=64, help="scalar IPC instruction window")
    parser.add_argument("--sliding-window-vec", type=positive_int, default=8, help="vector IPC instruction window")
    parser.add_argument("--vf-report-json", action="store_true", help="also export <db_stem>_vf_report.json")
    args = parser.parse_args(argv)
    for field in dataclasses.fields(config):
        setattr(config, field.name, getattr(args, field.name))
    try:
        generate_reports(args.input_db, args.output)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"prof_demo: {exc}", file=sys.stderr)
        return 1
    print(f"Reports: {args.output}")
    return 0
