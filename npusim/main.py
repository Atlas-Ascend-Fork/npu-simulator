# -*- coding: utf-8 -*-
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

"""
cannsim - Command-line tool for performance simulation analysis
"""

import argparse
import sys
from pathlib import Path

from npusim.core.record import record, RecordConfig, ktest, KtestConfig
from npusim.core.record.config import normalize_gen_report, preprocess_gen_report_argv
from npusim.core.report import report
from npusim.core.public.comm import welcome


def _add_record_parser(subparsers):
    """Add record command parser to subparsers."""
    parser = subparsers.add_parser("record", help="Run user application in AscendOps simulation environment")
    parser.add_argument(
        "user_app",
        nargs="?",
        default=None,
        help="Path or command to run (e.g. ./app, python train.py, bash run.sh). "
        "Required when -c is not specified; not allowed when -c is specified.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=".",
        help="Output directory for simulation results (default: current directory)",
    )
    parser.add_argument(
        "-s",
        "--soc-version",
        default=None,
        help="Target chip version for simulation (e.g., Ascend950). "
        "Required when -c is not specified; not allowed when -c is specified.",
    )
    parser.add_argument(
        "-g",
        "--gen-report",
        nargs="?",
        const=True,
        default=False,
        help="Generate report when simulation ends. Use '-g vf' to additionally export VF IPC JSON.",
    )
    parser.add_argument(
        "-u",
        "--user-option",
        default="",
        help="User-defined operator parameters as command-line options (optional).",
    )
    parser.add_argument(
        "-n",
        "--core-id",
        default="",
        help="AI core(s) whose logs are enabled during simulation via "
        "CORE_ENABLE_MASK. Same format as `npusim report -n`: "
        "'all', '0-2,12-14', '5'. "
        "Defaults to the model's full-open behavior; when -g is set and "
        "this is omitted, the auto-report falls back to core 0.",
    )
    parser.add_argument(
        "-c",
        "--case-dir",
        default=None,
        help="Path to the case directory for ktest mode. When specified, "
        "user_app and -s/--soc-version are not allowed; the tool runs "
        "camodel ./sim with the case directory symlinked as 'case'.",
    )
    parser.add_argument(
        "--pv",
        action="store_true",
        default=False,
        help="Use programmer-view (PV) simulation: functional only, faster, "
        "without accurate timing. If not specified, accurate timing simulation "
        "is used.",
    )
    parser.add_argument(
        "--chip",
        type=int,
        default=0,
        help="Number of simulated NPU chips in the comm domain. "
        "Sets CAMODEL_SIM_RANK_SIZE. When >1 the collective-communication (CC) simulation "
        "mock stack is auto-enabled (HCCL mock injection + comm env). "
        "Defaults to $CAMODEL_SIM_RANK_SIZE (or 1).",
    )


def _add_report_parser(subparsers):
    """Add report command parser to subparsers."""
    parser = subparsers.add_parser("report", help="Generate performance analysis reports")
    parser.add_argument(
        "-e",
        "--export",
        required=True,
        help="Folder containing the simulation model execution results",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Folder where the pipeline diagrams will be generated (default: same as export directory)",
    )
    parser.add_argument(
        "-n",
        "--core-id",
        help="Core ID(s) to use. Support formats: all, 0-2,12-14, 5",
    )
    parser.add_argument(
        "--smart-pipes",
        action="store_true",
        help="Also generate smart pipe tracing JSON",
    )
    parser.add_argument(
        "-m",
        "--merge-trace",
        action="store_true",
        default=False,
        help="Merge per-core traces: generate both per-chip per-kernel "
        "trace_chip{N}_kernel{N}.json and a global trace_allchips.json "
        "(requires -n).",
    )
    parser.add_argument(
        "--object-file",
        default=None,
        help="Device object file (e.g. *.asc.o) compiled with -g. When set, "
        "detection reports annotate each violation's PC with source file:line "
        "(requires msobjdump + llvm-symbolizer from the CANN toolkit). "
        "Defaults to the executable recorded by `npusim record` (.user-app marker).",
    )


def setup_parser():
    """Setup and configure the argument parser."""
    parser = argparse.ArgumentParser(
        prog="npusim",
        description="Command-line tool for performance simulation analysis on Ascend hardware.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _add_record_parser(subparsers)
    _add_report_parser(subparsers)

    return parser


def handle_record_command(args, parser):
    """Handle the record command.

    Two mutually exclusive modes:
    - With -c/--case-dir: ktest mode (runs camodel ./sim with case symlink).
      user_app and -s/--soc-version are not allowed.
    - Without -c: classic record mode. user_app and -s/--soc-version are required.
    """
    has_case = bool(getattr(args, "case_dir", None))
    has_app = bool(args.user_app)
    has_soc = bool(args.soc_version)

    if has_case:
        offending = []
        if has_app:
            offending.append("user_app")
        if has_soc:
            offending.append("-s/--soc-version")
        if offending:
            parser.error(f"argument(s) not allowed with -c/--case-dir: {', '.join(offending)}")
        cfg = KtestConfig(
            case_dir=Path(args.case_dir),
            output=Path(args.output),
            gen_report=normalize_gen_report(args.gen_report),
            model="PV" if args.pv else "CA",
        )
        return 0 if ktest(cfg) else 1

    missing = []
    if not has_app:
        missing.append("user_app")
    if not has_soc:
        missing.append("-s/--soc-version")
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    cfg = RecordConfig(
        user_app=Path(args.user_app),
        output=Path(args.output),
        soc_version=args.soc_version,
        gen_report=normalize_gen_report(args.gen_report),
        user_option=args.user_option,
        object_file=getattr(args, "object_file", "") or "",
        core_id=getattr(args, "core_id", "") or "",
        model="PV" if args.pv else "CA",
        chip=getattr(args, "chip", 0) or 0,
    )
    return 0 if record(cfg) else 1


def handle_report_command(args):
    """Handle the report command."""
    # Call the report function with parsed arguments
    object_file = getattr(args, "object_file", None)
    smart_pipes = getattr(args, "smart_pipes", False)
    merge_trace = getattr(args, "merge_trace", False)
    return (
        0
        if report(args.export, args.output, args.core_id, object_file, smart_pipes=smart_pipes, merge_trace=merge_trace)
        else 1
    )


def main():
    """Command-line tool for performance simulation analysis on Ascend hardware.
    The simulation emulates real Ascend hardware behavior—including compute units,
    memory hierarchy, and scheduling—enabling accurate performance modeling
    without physical devices.

    Examples:
        $ npusim record ./app -s Ascend950 -o ./output
        $ npusim record ./app -s Ascend950 -o ./output -u "--input data.json"
        $ npusim record -c /path/to/case -o ./output
        $ npusim report -e ./output/sim -o ./output/trace.json

    Note:
        - Input app must be a valid AscendOps-built executable.
        - Output directories are auto-created.
        - `trace.json` is compatible with Chrome tracing tools.
        - Advanced reporting features (e.g., HTML, diagrams) are planned but not yet available.
    """

    print(welcome)

    parser = setup_parser()

    # Parse arguments
    args = parser.parse_args(preprocess_gen_report_argv(sys.argv[1:]))

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "record":
        return handle_record_command(args, parser)
    elif args.command == "report":
        return handle_report_command(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
