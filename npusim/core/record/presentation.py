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

"""Terminal presentation helpers for cannsim record."""

import shlex
import sys
from datetime import datetime, timezone
from typing import Optional

from npusim.core.public.comm import (
    COLOR_CYAN,
    COLOR_GREEN,
    COLOR_GREY,
    COLOR_RESET,
    COLOR_YELLOW,
    STYLE_BOLD,
    USE_COLOR,
    print_error,
    render_reference_benchmarks,
)
from npusim.core.public.typical_operator_times import TYPICAL_OPERATOR_TIMES


def fmt_accent(text: str, color: str = COLOR_CYAN) -> str:
    if not USE_COLOR:
        return text
    return f"{color}{text}{COLOR_RESET}"


def now_local_str() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def fmt_returncode(returncode: Optional[int]) -> str:
    if returncode is None:
        return ""
    if returncode < 0:
        return f"signal {-returncode}"
    return f"exit {returncode}"


def print_stage_ok(idx: int, name: str, extra: str = "") -> None:
    dot = fmt_accent("OK", COLOR_GREEN) if USE_COLOR else "OK"
    line = f"[{idx}/3] {name:<24} {dot}"
    if extra:
        line += f"  {fmt_accent(extra, COLOR_GREY)}"
    print(line)


def print_stage_fail(idx: int, reason: str, context: dict) -> bool:
    tag = fmt_accent("FAIL", COLOR_YELLOW + STYLE_BOLD) if USE_COLOR else "FAIL"
    stage_name = {1: "Preflight", 2: "Paths resolved", 3: "Simulation"}.get(idx, f"Stage {idx}")
    print(f"[{idx}/3] {stage_name:<24} {tag}")
    sys.stdout.flush()
    print_error(reason)
    sys.stderr.flush()

    if context:
        key_w = max((len(k) for k in context), default=0)
        print()
        print("  Context:")
        for k, v in context.items():
            print(f"    {k:<{key_w}}   {v}")
    return False


def print_stage3_preamble(
    argv,
    cwd,
    origin_soc_version: str,
    launch_kind: str,
    core_id: str,
    user_option: str,
    gen_report: str,
) -> None:
    print()
    print(render_reference_benchmarks(TYPICAL_OPERATOR_TIMES))
    print()

    cmdline = " ".join(shlex.quote(x) for x in argv)
    accent = fmt_accent("What will run", COLOR_CYAN + STYLE_BOLD) if USE_COLOR else "What will run"
    print(f"  {accent}")
    print(f"   cwd             {cwd}")
    print(f"   cmd             {cmdline}")
    print(f"   soc             {origin_soc_version}")
    print(f"   python-env      {sys.executable}")
    print(f"   launch-kind     {launch_kind}")

    if core_id:
        print(f"   core-id         {core_id}")
    if user_option:
        print(f"   user-opt        {user_option}")
    if gen_report:
        print("   report          yes")


def print_start_banner() -> None:
    print(
        f"\n===============================================================================\n"
        f"    AscendC Kernel Simulation Tool  -  Start ({now_local_str()})\n"
        f"==============================================================================="
    )


def print_record_summary(state, success: bool) -> None:
    if state._interrupted:
        status = fmt_accent("INTERRUPTED", COLOR_YELLOW + STYLE_BOLD) if USE_COLOR else "INTERRUPTED"
    elif success:
        status = fmt_accent("SUCCESS", COLOR_GREEN + STYLE_BOLD) if USE_COLOR else "SUCCESS"
    else:
        status = fmt_accent("FAILED", COLOR_YELLOW + STYLE_BOLD) if USE_COLOR else "FAILED"

    dash = "─" * 72
    header = f"Simulation {status}  ·  run time {state._last_run_elapsed:.1f}s"
    rc_text = fmt_returncode(state._last_returncode)
    if rc_text and not success:
        header += f"  ·  {rc_text}"
    dash_ref = state._last_dashboard
    if dash_ref and dash_ref.detector.hw_sim_times:
        times = dash_ref.detector.hw_sim_times
        header += f"  ·  hardware sim {' / '.join(f'{t:.2f}s' for t in times)}"

    print(dash)
    print(header)

    if state.target_dir:
        print()
        print("Artifacts")
        print(f"  archive   {state.target_dir}")
        print(f"  log       {state.cannsim_log}")
        vf_report_files = list(state.target_dir.rglob("*_vf_report.json"))
        if vf_report_files:
            print(f"  vf_json   {state.target_dir} ({len(vf_report_files)} file(s))")

    if dash_ref is not None:
        warnings = dash_ref.warnings_summary
        if warnings:
            total = sum(n for _, n in warnings)
            print()
            print(f"Warnings ({len(warnings)} unique / {total} total)")
            for msg, count in warnings[:10]:
                short = msg if len(msg) <= 100 else msg[:97] + "..."
                print(f"  x{count:<4} {short}")
            if len(warnings) > 10:
                print(f"  (... {len(warnings) - 10} more, see npusim.log)")

    if state._interrupted and dash_ref is not None:
        events = dash_ref.events
        phase = dash_ref.detector.phase or "(waiting)"
        progress = dash_ref.detector.progress_label()
        print()
        print("Captured progress")
        print(f"  phase     {phase}")
        if progress:
            print(f"  progress  {progress}")
        print(f"  events    {len(events)}")
        for ev in events[-5:]:
            print(f"  - [{ev.level}] {ev.message}")

    if not success and state._last_stderr_tail:
        print()
        print("Last log lines")
        for line in state._last_stderr_tail.rstrip().splitlines():
            print(f"  {line}")

    if success and state.target_dir:
        print()
        print("Next")
        print(f"  $ npusim report -e {state.target_dir}")
    print(dash)
