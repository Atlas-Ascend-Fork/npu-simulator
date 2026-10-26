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
from typing import Optional

from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.frontend.tracing.chrome_tracing_default import ChromeTracingBuilder
from npusim.prof.src.frontend.tracing.chrome_tracing_smart_pipe import (
    SmartPipeTracingBuilder,
)
from npusim.prof.src.frontend.tracing.chrome_tracing_aicore_occupancy import (
    AiCoreOccupancyTraceBuilder,
)
from npusim.prof.src.frontend.tracing.chrome_tracing_critical_path import (
    CriticalPathBuilder,
)
from npusim.prof.src.arch_diagram.reporter import ArchDiagramReporter
from npusim.prof.src.arch_diagram.constants import ARCH_DIAG_SUPPORTED_ARCH
from npusim.prof.src.frontend.summary_reporter import SummaryReporter
from npusim.prof.src.frontend.bubble_report import BubbleReporter


class HotspotsProcessor:
    def __init__(
        self,
        arch: str,
        workspace: str,
        log_folder: str,
        log_files: list,
        object_path: Optional[str] = None,
        kernel_name: str = "kernel",
        kernel_name_short: str = "kernel",
        kernel_addr: int = 0x0,
        report_dir_name: Optional[str] = None,
    ) -> None:
        self.arch = arch
        self.log_folder = log_folder
        self.object_path = object_path
        self.workspace = workspace
        self.kernel_name = kernel_name
        self.kernel_name_short = kernel_name_short
        self.kernel_addr = kernel_addr
        self.report_dir_name = report_dir_name or f"{kernel_name_short}_reports"
        self.report_dir_path = os.path.join(workspace, self.report_dir_name)
        os.mkdir(self.report_dir_path)

    def generate_chrome_tracing(self) -> int:
        builder = ChromeTracingBuilder()
        occupancy_builder = AiCoreOccupancyTraceBuilder()
        res = builder.generate(self.report_dir_path)
        if biprof_ctx.smart_pipes:
            smart_pipe_builder = SmartPipeTracingBuilder()
            res += smart_pipe_builder.generate(self.report_dir_path)
        res += occupancy_builder.generate(self.report_dir_path)
        if biprof_ctx.critical_path:
            cpath_builder = CriticalPathBuilder()
            res += cpath_builder.generate(self.report_dir_path)
        return res

    def generate_arch_diagram(self) -> int:
        if self.arch in ARCH_DIAG_SUPPORTED_ARCH:
            adreporter = ArchDiagramReporter(self.report_dir_path)
            return adreporter.process()
        return 0

    def generate_bubble_report(self) -> int:
        reporter = BubbleReporter()
        reporter.prepare_report(self.report_dir_path)
        return 0

    def generate_summary_report(self) -> int:
        summary_reporter = SummaryReporter()
        return summary_reporter.prepare_report(self.report_dir_path)
