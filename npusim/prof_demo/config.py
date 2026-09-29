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
"""Options for one sequential report session (not a concurrent API)."""

from dataclasses import dataclass


@dataclass
class ReportConfig:
    core_id: str = "all"
    no_ticks_to_ns: bool = False
    sliding_window: int = 64
    sliding_window_vec: int = 8
    json_max_size: int = 524288000
    vf_report_json: bool = False


config = ReportConfig()
