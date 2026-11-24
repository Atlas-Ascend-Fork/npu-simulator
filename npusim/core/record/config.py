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

"""Configuration types and CLI argument normalization for cannsim record."""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

TIMEOUT_ENV_VAR = "NPUSIM_TIMEOUT"

_DURATION_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$", re.IGNORECASE)


@dataclass
class RecordConfig:
    """Input contract for the cannsim record command."""

    user_app: Union[str, Path]
    output: Path
    soc_version: str
    gen_report: str = ""
    user_option: str = ""
    object_file: str = ""
    core_id: str = ""
    model: str = "CA"
    chip: int = 0
    feature_list: Optional[str] = None
    timeout: float = 0.0
    # 由 record() 在执行结束后回填（非输入参数）：仿真是否因超时被终止。
    # 供 CLI 层映射超时专用退出码，编程调用方可忽略。
    timed_out: bool = False


def parse_duration(raw) -> float:
    """把超时时长归一化为秒。

    只接受秒：纯数字（如 ``90``、``1.5``）或带 ``s`` 后缀的字符串
    （如 ``30s``，大小写不敏感）；数字类型直接按秒解释。
    ``0`` 表示不限制。分钟 / 小时等其他单位（``30m``、``2h``）、
    负数、空串、复合写法等非法输入抛 ValueError。
    """
    if raw is None:
        return 0.0
    if isinstance(raw, bool):
        raise ValueError(f"invalid timeout value: {raw!r}")
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value < 0:
            raise ValueError(f"negative timeout is not allowed: {raw!r}")
        return value
    match = _DURATION_PATTERN.match(str(raw))
    if match is None:
        raise ValueError(
            f"invalid timeout value {raw!r}: expected seconds (a plain number "
            f"or with an 's' suffix, e.g. 30 or 30s); 0 disables the limit"
        )
    return float(match.group(1))


def format_duration(seconds: float) -> str:
    """把秒格式化成人类可读的短时长（7200 -> '2h'，1800 -> '30m'，90 -> '90s'）。"""
    seconds = float(seconds)
    if seconds <= 0:
        return "0s"
    for unit, size in (("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    return f"{seconds:g}s"


def resolve_timeout(cli_value, env=None) -> float:
    """解析生效的超时时长：显式 CLI 值 > $NPUSIM_TIMEOUT > 0（不限制）。

    CLI 传入 ``0`` 视为显式关闭限制，优先于环境变量。非法输入（CLI 或环境
    变量来源）抛 ValueError，由调用方决定报错形式。
    """
    if cli_value is not None:
        return parse_duration(cli_value)
    if env is None:
        env = os.environ
    raw = (env.get(TIMEOUT_ENV_VAR) or "").strip()
    if not raw:
        return 0.0
    return parse_duration(raw)


def normalize_gen_report(raw) -> str:
    """Normalize -g argument: True -> "true", "vf" -> "vf", False -> ""."""
    if raw is True:
        return "true"
    if raw is False:
        return ""
    return str(raw).lower()


def preprocess_gen_report_argv(argv):
    """Rewrite -g/--gen-report so argparse never greedily swallows the next token.

    argparse `nargs="?"` consumes the following non-option token as the option
    value, so `-g python3 test_add.py` steals the positional user_app and fails
    with ``required: user_app``. Only the literal ``vf`` is meaningful, so emit
    an explicit, non-greedy ``--gen-report=<value>`` form instead.
    """
    out = []
    i, n = 0, len(argv)
    while i < n:
        tok = argv[i]
        if tok in ("-g", "--gen-report"):
            if i + 1 < n and argv[i + 1].lower() == "vf":
                out.append("--gen-report=vf")
                i += 2
                continue
            out.append("--gen-report=true")
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def normalize_sim_mode(raw) -> str:
    """Normalize --sim_mode value: "ca"/"pv" (case-insensitive) -> "CA"/"PV"."""
    mode = str(raw).lower()
    if mode == "pv":
        return "PV"
    if mode == "ca":
        return "CA"
    raise ValueError(f"invalid sim_mode: {raw!r} (expected 'ca' or 'pv')")


def build_record_config_from_args(args) -> RecordConfig:
    return RecordConfig(
        user_app=args.user_app,
        output=Path(args.output),
        soc_version=args.soc_version,
        gen_report=normalize_gen_report(args.gen_report),
        user_option=args.user_option,
        object_file=getattr(args, "object_file", "") or "",
        core_id=getattr(args, "core_id", "") or "",
        model=normalize_sim_mode(getattr(args, "sim_mode", "ca")),
        chip=getattr(args, "chip", 0) or 0,
        feature_list=getattr(args, "feature_list", None),
        timeout=resolve_timeout(getattr(args, "timeout", None)),
    )
