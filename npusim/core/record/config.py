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

from dataclasses import dataclass
from pathlib import Path
from typing import Union


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


def build_record_config_from_args(args) -> RecordConfig:
    return RecordConfig(
        user_app=Path(args.user_app),
        output=Path(args.output),
        soc_version=args.soc_version,
        gen_report=normalize_gen_report(args.gen_report),
        user_option=args.user_option,
        object_file=getattr(args, "object_file", "") or "",
        core_id=getattr(args, "core_id", "") or "",
        model="PV" if getattr(args, "pv", False) else "CA",
        chip=getattr(args, "chip", 0) or 0,
    )
