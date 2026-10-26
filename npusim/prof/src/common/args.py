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

import argparse
import os
import shlex
import logging as log
from typing import List, Optional

import npusim.prof.src.version as version
from npusim.prof.src.common.biprof_enum import (
    AnalysisType,
    ArchName,
    ArgumentAction,
    BiprofMode,
    LogLevel,
    user_visible_archs,
)


class BiProfArgParser:
    @classmethod
    def _get_versions(cls) -> str:
        biprof_tool_ver = f"v{version.MAJOR_VERSION}.{version.MINOR_VERSION}.{version.PATCH_VERSION}"
        return f"npu-emulate tool: {biprof_tool_ver}\n"

    @staticmethod
    def _add_help(parser: argparse.ArgumentParser):
        other_args = parser.add_argument_group("Other")
        other_args.add_argument("--help", action="help", help="Show this help message and exit")

    @classmethod
    def _get_all_parser_args(cls, p: argparse.ArgumentParser) -> List[str]:
        all_args: List[str] = []
        for action in p._actions:
            all_args.extend(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                for _, sp in action.choices.items():
                    all_args.extend(cls._get_all_parser_args(sp))
        return all_args

    def __init__(self) -> None:
        self._parser = argparse.ArgumentParser(
            prog="npu-emulate",
            description="Profiling tool for Ascend operators",
            formatter_class=argparse.RawTextHelpFormatter,
            add_help=False,
        )
        self._parser.add_argument(
            "--version",
            action=ArgumentAction.VERSION,
            version=self._get_versions(),
            help="Show npu-emulate version",
        )
        self._op_modes = self._parser.add_subparsers(required=True)
        self._add_help(self._parser)

    def _report_mode(self) -> argparse.ArgumentParser:
        report_mode = self._op_modes.add_parser(
            "report",
            help=("Reports generation mode. Use 'npu-emulate report --help' to see more"),
            add_help=False,
        )
        report_mode.set_defaults(mode=BiprofMode.REPORT)

        # -- Required arguments --
        required_args = report_mode.add_argument_group("Required arguments")
        required_args.add_argument(
            "--input-db",
            action=ArgumentAction.STORE,
            type=str,
            required=True,
            help=("Specify an existing contract SQLite DB file or a directory containing contract DB files."),
        )

        # -- Optional architecture / object file --
        optional_args = report_mode.add_argument_group("Optional arguments")
        optional_args.add_argument(
            "-a",
            "--arch",
            default=None,
            choices=user_visible_archs,
            metavar="{" + ",".join(user_visible_archs) + "}",
            action=ArgumentAction.STORE,
            help=(
                "Specify the Ascend architecture. When omitted, the arch "
                f"is derived from the contract DB (default: {ArchName.V100})"
            ),
        )
        optional_args.add_argument(
            "-t",
            "--analysis-type",
            type=AnalysisType,
            default=AnalysisType.HOTSPOTS,
            choices=list(AnalysisType),
            action=ArgumentAction.STORE,
            help=(
                f"Specify the analysis type for which the report should be generated (default: {AnalysisType.HOTSPOTS})"
            ),
        )
        optional_args.add_argument(
            "--object-file",
            action=ArgumentAction.STORE,
            type=str,
            help="Specify the path to the device object file (optional)",
        )

        # -- Processing --
        processing_args = report_mode.add_argument_group("Processing")
        processing_args.add_argument(
            "--kernel-name",
            action=ArgumentAction.STORE,
            type=str,
            help="Specify the kernel to be profiled",
        )
        processing_args.add_argument(
            "--core-id",
            action=ArgumentAction.STORE,
            default="0",
            type=str,
            help=("Specify core IDs for report generation. Formats: 'all', '0', '0-2', '0-2,12-14,5' (default: 0)"),
        )
        processing_args.add_argument(
            "--strict",
            action=ArgumentAction.STORE_TRUE,
            help="Set strict mode for report generation",
        )
        processing_args.add_argument(
            "--force-disassembly",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Disable hidumper pattern matching",
        )
        processing_args.add_argument(
            "--disable-analyzers",
            action=ArgumentAction.STORE,
            default="",
            help="Specify analyzers to disable (comma-separated)",
        )
        processing_args.add_argument(
            "--disable-hints",
            action=ArgumentAction.STORE,
            default="",
            help="Specify hints to disable (comma-separated)",
        )
        processing_args.add_argument(
            "-j",
            "--jobs",
            action=ArgumentAction.STORE,
            default=8,
            type=int,
            help=("Specify the maximum number of jobs for parallel execution (default: 8)"),
        )

        # -- Extra modes --
        extra_args = report_mode.add_argument_group("Extra modes")
        extra_args.add_argument(
            "--smart-pipes",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help=("Show actual instruction execution time in pipe without 'waiting overlaps'."),
        )
        extra_args.add_argument(
            "--critical-path",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable critical path analysis",
        )
        extra_args.add_argument(
            "--memory-utilization",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help=("Enable memory utilization analyzers (can take a long time)"),
        )
        extra_args.add_argument(
            "--scalar-ipc",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable scalar IPC dynamic analysis",
        )
        extra_args.add_argument(
            "--vec-ipc",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable vector IPC dynamic analysis",
        )
        extra_args.add_argument(
            "--call-stack",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable call stack analysis",
        )
        extra_args.add_argument(
            "--matrix-copy",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable matrix copying visualizer",
        )
        extra_args.add_argument(
            "--simd-sampling",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable SIMD Stall reason analysis",
        )
        extra_args.add_argument(
            "--simd-perf",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable SIMD performance analysis (VfPMU top-down)",
        )
        extra_args.add_argument(
            "--bubble",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Enable bubble analysis",
        )

        # -- Scalar/Vector IPC dynamic analysis --
        ipc_dynamic = report_mode.add_argument_group("Scalar/Vector IPC dynamic analysis")
        ipc_dynamic.add_argument(
            "--sliding-window",
            action=ArgumentAction.STORE,
            default=64,
            type=int,
            help=("Scalar IPC sliding window size in instructions (default: 64)"),
        )
        ipc_dynamic.add_argument(
            "--sliding-window-vec",
            action=ArgumentAction.STORE,
            default=8,
            type=int,
            help=("Vector IPC sliding window size in instructions (default: 8)"),
        )

        # -- Output options --
        output_args = report_mode.add_argument_group("Output options")
        output_args.add_argument(
            "-o",
            "--output",
            action=ArgumentAction.STORE,
            default=os.path.join(os.getcwd(), "results"),
            help=("Specify the directory where the profiling results should be saved"),
        )
        output_args.add_argument(
            "--no-ticks-to-ns",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help=("Use hardware ticks instead of nanoseconds in Chrome Tracing reports"),
        )
        output_args.add_argument(
            "--save_tmp_files",
            action=ArgumentAction.STORE_TRUE,
            help="Do not remove temporary files after report generation",
        )
        output_args.add_argument(
            "--vf-report-json",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help="Export structured VF report JSON during report generation",
        )
        output_args.add_argument(
            "--multi-chip-report",
            action=ArgumentAction.STORE_TRUE,
            default=False,
            help=argparse.SUPPRESS,
        )
        output_args.add_argument(
            "--json-max-size",
            action=ArgumentAction.STORE,
            default=524288000,
            help=(
                "Specify the maximum size for Chrome Tracing report "
                "(in bytes). Default value is 500MB. "
                "Set -1 to disable report truncating by size"
            ),
        )

        # -- Logging level --
        logging_args = report_mode.add_argument_group("Logging")
        logging_args.add_argument(
            "-l",
            "--log-level",
            action=ArgumentAction.STORE,
            default=LogLevel.INFO,
            choices=[
                LogLevel.NOTSET,
                LogLevel.DEBUG,
                LogLevel.INFO,
                LogLevel.WARNING,
                LogLevel.ERROR,
                LogLevel.CRITICAL,
            ],
            help=f"Specify the logging level (default: {LogLevel.INFO})",
        )

        return report_mode

    def parse(self, args: List[str]) -> Optional[argparse.Namespace]:
        biprof_opts = os.environ.get("BIPROF_OPTS")
        if biprof_opts:
            for option in shlex.split(biprof_opts)[::-1]:
                args.insert(1, option)

        report_mode = self._report_mode()
        for subparser in [report_mode]:
            self._add_help(subparser)

        if not args:
            self._parser.print_help()
            return None
        if len(args) == 1 and args[0] == BiprofMode.REPORT:
            report_mode.print_help()
            return None

        # Parsing pass 1: unknown arguments are allowed
        parsed_args, unknown_args = self._parser.parse_known_args(args)
        all_parser_args = self._get_all_parser_args(self._parser)

        if not unknown_args:
            return parsed_args

        # Handling unknown args with messages if possible
        for unknown_arg in unknown_args:
            if unknown_arg in all_parser_args:
                log.error(
                    f"Argument {unknown_arg} is not valid "
                    "in the current mode. "
                    "Please make sure you are using the correct BiProf mode."
                )

        # Parsing pass 2: unknown arguments are disallowed
        parsed_args = self._parser.parse_args(args)
        return parsed_args
