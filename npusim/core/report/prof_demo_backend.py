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

"""Run prof_demo from the legacy biprof report argument list.

npusim report / ``-g`` still builds the old ``report -a v100 --input-db ...``
argv. prof_demo only reads helper DBs and refuses to overwrite its output
directory, so this adapter maps that argv onto ``output/results`` — the same
directory the old runner used — and replaces that directory on each run.
"""

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Optional


def prof_demo_available() -> bool:
    try:
        import npusim.prof_demo.cli  # noqa: F401
    except ImportError:
        return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-a", "--arch", default="v100")
    parser.add_argument("-o", "--output")
    parser.add_argument("--input-db")
    parser.add_argument("--binary-log")
    parser.add_argument("--camodel-log")
    parser.add_argument("--core-id", default="0")
    parser.add_argument("--vf-report-json", action="store_true")
    parser.add_argument("--smart-pipes", action="store_true")
    parser.add_argument("--no-ticks-to-ns", action="store_true")
    parser.add_argument("--sliding-window")
    parser.add_argument("--sliding-window-vec")
    parser.add_argument("--json-max-size")
    return parser


def run_prof_demo(argv: List[str]) -> int:
    """Translate one legacy report invocation. Returns a process exit code."""
    from npusim.core.public.comm import print_info, print_warn

    args = list(argv)
    if args and args[0] == "report":
        args = args[1:]
    known, _unknown = _parser().parse_known_args(args)
    if known.binary_log or known.camodel_log or not known.input_db:
        print_warn(
            "prof_demo only reads cannsim_helper DB files. "
            "instr.bin / log_ca report input is no longer supported."
        )
        return 1
    if known.arch != "v100":
        print_warn(f"prof_demo only supports arch v100, got {known.arch}")
        return 1
    if not known.output:
        print_warn("report output directory is required")
        return 1
    if known.smart_pipes:
        print_warn("smart-pipes is not implemented in prof_demo; JSON reports omit that view")

    results = Path(known.output).resolve() / "results"
    if results.exists() or results.is_symlink():
        shutil.rmtree(results)
    demo_argv = [
        "--input-db",
        known.input_db,
        "-o",
        str(results),
        "--core-id",
        known.core_id or "0",
    ]
    if known.vf_report_json:
        demo_argv.append("--vf-report-json")
    if known.no_ticks_to_ns:
        demo_argv.append("--no-ticks-to-ns")
    if known.sliding_window:
        demo_argv.extend(["--sliding-window", known.sliding_window])
    if known.sliding_window_vec:
        demo_argv.extend(["--sliding-window-vec", known.sliding_window_vec])
    if known.json_max_size:
        demo_argv.extend(["--json-max-size", known.json_max_size])

    print_info(f"Running prof_demo for {known.input_db}")
    from npusim.prof_demo.cli import main

    return main(demo_argv)


def run_prof_demo_or_exit(argv: Optional[List[str]] = None) -> None:
    code = run_prof_demo(sys.argv[1:] if argv is None else argv)
    if code:
        raise SystemExit(code)
