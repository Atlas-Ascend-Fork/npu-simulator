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
from npusim.prof_demo.config import config


def asdict_nocopy(obj, cond=lambda x: True):
    if hasattr(obj, "__dict__"):
        return {key: asdict_nocopy(value, cond) for key, value in obj.__dict__.items() if cond(value)}
    return obj


def decompose_instr_id(composite_id):
    """Low 32 bits of the composite ExecInstrId (core/core-type bits dropped)."""
    return composite_id & ((1 << 32) - 1)


def edge_instructions(instructions, window_size):
    """Leading/trailing instructions not covered by any sliding-window midpoint.

    Shared by scalar and VF dynamic IPC; callers let them inherit the first/last
    window value (imputation for visualization continuity).
    """
    start_instructions, end_instructions = [], []
    if len(instructions) >= 2:
        start_instructions = instructions[: int(window_size / 2)]
    if len(instructions) > 2:
        is_even = window_size % 2 == 0
        tail = int(window_size / 2) - is_even
        end_instructions = instructions[-tail:] if tail > 0 else []
    return start_instructions, end_instructions


def get_tick_to_us_coeff():
    from npusim.prof_demo.database import database_handler

    # Chrome Trace timestamps are always microseconds. Legacy ticks mode
    # treats one tick as one ns while retaining displayTimeUnit="ns".
    return 0.001 if config.no_ticks_to_ns else 0.001 / float(database_handler.get_frequency_ghz())


class ChromeTracingUtils:
    @staticmethod
    def percents_or_none(value):
        return None if value is None else f"{value}%"

    @staticmethod
    def round_or_none(value, ndigits):
        return None if value is None else float(round(value, ndigits))
