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

from npusim.core.public import comm, soc_info, typical_operator_times, dashboard
from npusim.core.report.report import generate_report, report
from npusim.core.record import record, RecordConfig

__all__ = [
    "comm",
    "soc_info",
    "typical_operator_times",
    "dashboard",
    "report",
    "generate_report",
    "record",
    "RecordConfig",
]
