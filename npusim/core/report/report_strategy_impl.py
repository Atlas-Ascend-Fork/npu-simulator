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

"""Concrete report strategy implementations and the execution chain."""

from pathlib import Path
from typing import List, Optional, Tuple

from npusim.core.report.prof_demo_backend import prof_demo_available, run_prof_demo
from npusim.core.report.report_strategy_base import (
    BIPROF_BACKEND_NAME,
    get_biprof_runner_module,
    PROF_DEMO_BACKEND_NAME,
    ReportStrategy,
    SOC_TO_ARCH,
)


# =============================================================================
# Concrete Strategies
# =============================================================================


class BiProfModuleStrategy(ReportStrategy):
    """Layer 1: BiProfRunner pure-Python module."""

    @property
    def name(self) -> str:
        return BIPROF_BACKEND_NAME

    def is_available(self) -> bool:
        return get_biprof_runner_module() is not None

    def execute(
        self,
        biprof_args: List[str],
        _export_abs: Path,
        _output_abs: Path,
        _core_ids: tuple,
    ) -> Tuple[str, bool]:
        from npusim.core.public.comm import print_info, print_warn

        _runner = get_biprof_runner_module()
        if _runner is None:
            return (self.name, False)
        print_info("Running ProfRunner module...")
        try:
            retcode = _runner.run(biprof_args)
        except Exception as e:
            print_warn(f"Failed to run ProfRunner module: {e}")
            return (self.name, False)

        if retcode == 0:
            print_info("Report generation completed successfully.")
            return (self.name, True)

        print_warn(f"BiProfRunner module failed (return code {retcode}).")
        return (self.name, False)


class ProfDemoStrategy(ReportStrategy):
    """Layer 2: stdlib-only prof_demo fallback (JSON reports)."""

    @property
    def name(self) -> str:
        return PROF_DEMO_BACKEND_NAME

    def is_available(self) -> bool:
        return prof_demo_available()

    def execute(
        self,
        biprof_args: List[str],
        _export_abs: Path,
        _output_abs: Path,
        _core_ids: tuple,
    ) -> Tuple[str, bool]:
        from npusim.core.public.comm import print_info, print_warn

        if not self.is_available():
            return (self.name, False)
        print_info("Running prof_demo...")
        try:
            retcode = run_prof_demo(biprof_args)
        except Exception as e:
            print_warn(f"Failed to run prof_demo: {e}")
            return (self.name, False)

        if retcode == 0:
            print_info("Report generation completed successfully.")
            return (self.name, True)

        print_warn(f"prof_demo failed (return code {retcode}).")
        return (self.name, False)


# =============================================================================
# Execution Chain
# =============================================================================


class FallbackChain:
    """Try each strategy in order; return (backend_name, success) of the first successful one."""

    def __init__(self, strategies: List[ReportStrategy]):
        self._strategies = strategies

    def execute(
        self,
        biprof_args: List[str],
        export_abs: Path,
        output_abs: Path,
        core_ids: tuple,
    ) -> Tuple[str, bool]:
        from npusim.core.public.comm import print_error

        for strategy in self._strategies:
            if not strategy.is_available():
                continue
            backend, ok = strategy.execute(biprof_args, export_abs, output_abs, core_ids)
            if ok:
                return (backend, True)
        print_error(_format_backend_failure_message(output_abs))
        return ("", False)


# BiProfRunner writes its log to {output_abs}/results/runner.py.log.
_BIPROF_LOG_REL = Path("results") / "runner.py.log"


def _extract_biprof_error(output_abs: Path) -> Optional[str]:
    """Extract the last backend ERROR line from the BiProfRunner log."""
    log_path = Path(output_abs) / _BIPROF_LOG_REL
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    last_error = None
    for line in text.splitlines():
        if "[ERROR]" in line:
            last_error = line.strip()
    return last_error


def _format_backend_failure_message(output_abs: Path) -> str:
    """Prefer the real backend root cause, then fall back to install hint."""
    root_cause = _extract_biprof_error(output_abs)
    if root_cause:
        return (
            "BiProfRunner module failed.\n"
            f"Root cause: {root_cause}\n"
            "Full log: "
            f"{Path(output_abs) / _BIPROF_LOG_REL}\n"
            "Common cause: simulation produced no valid instruction-level "
            "trace (empty instr.bin / *.instr_log.dump). "
            "Re-run the simulation with instruction logging enabled."
        )
    return (
        "No report backend succeeded.\n"
        "Searched: Python module BiProfRunner, prof_demo.\n"
        "Please reinstall the cannsim wheel that provides both report backends."
    )


# Pre-built chain: full BiProfRunner report first, stdlib-only prof_demo fallback.
REPORT_CHAIN = FallbackChain(
    [
        BiProfModuleStrategy(),
        ProfDemoStrategy(),
    ]
)


# --------------------------------------------------------------------------
# Backwards-compatible private aliases — callers using the old underscore
# names from report_strategy.py continue to work without any import changes.
# --------------------------------------------------------------------------
_ReportStrategy = ReportStrategy
_BiProfModuleStrategy = BiProfModuleStrategy
_ProfDemoStrategy = ProfDemoStrategy
_FallbackChain = FallbackChain
_REPORT_CHAIN = REPORT_CHAIN
_SOC_TO_ARCH = SOC_TO_ARCH
