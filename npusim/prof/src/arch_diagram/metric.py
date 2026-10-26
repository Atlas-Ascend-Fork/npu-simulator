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

from math import isclose
from dataclasses import dataclass
from collections import defaultdict
from typing import Dict

from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.biprof_enum import ArchName
from npusim.prof.src.arch_diagram.constants import MEMORY_BANDWIDTH_V100


@dataclass
class MetricData:
    formatted_value: str
    relative_tp: float = 0.0


class MetricFormat:
    @staticmethod
    def format_time(raw_value: float) -> MetricData:
        value = f"{raw_value:.2f} us"
        return MetricData(value)

    @staticmethod
    def format_throughput(raw_value: float) -> MetricData:
        if not isclose(raw_value, 0.0) and raw_value < 0.01:
            value = "<0.01 GB/s"
        else:
            value = f"{raw_value:.2f} GB/s"
        return MetricData(value)

    @staticmethod
    def format_throughput_with_peak(raw_value: float, peak_value: float) -> MetricData:
        metric_data = MetricFormat.format_throughput(raw_value)
        if peak_value == 0.0:
            return metric_data
        metric_data.relative_tp = (raw_value / peak_value) * 100
        return metric_data

    @staticmethod
    def format_count(raw_value: float) -> MetricData:
        value = f"{int(raw_value)}"
        return MetricData(value)

    @staticmethod
    def format_usage(raw_value: float) -> MetricData:
        value = f"{raw_value:.2f}%"
        return MetricData(value)


class Metric:
    METRICS_FORMATTER = {
        "time": MetricFormat.format_time,
        "tp": MetricFormat.format_throughput,
        "count": MetricFormat.format_count,
        "usage": MetricFormat.format_usage,
    }
    name: str
    data: MetricData

    def __init__(self, name: str, format_type: str = ""):
        self.name = name
        self.data = MetricData("No formatter")

        self._strip_name = name
        if name.endswith("_gbs"):
            self._strip_name = name[:-4]
        elif name.endswith("_ratio"):
            self._strip_name = name[:-6]
        self._format_type = format_type
        self._formatter = self.METRICS_FORMATTER.get(format_type)

    def format(self, raw_value: float) -> MetricData:
        # specific formatter for throughput metrics with known peaks
        bw_peaks = self.get_bw_peaks()
        if self._format_type == "tp" and self._strip_name in bw_peaks:
            peak_value = bw_peaks.get(self._strip_name)
            if peak_value is None:
                peak_value = 0.0
            return MetricFormat.format_throughput_with_peak(raw_value, peak_value)
        # common formatters for other metrics
        if self._formatter is not None:
            return self._formatter(raw_value)

        # impossible case to make mypy happy
        return self.data

    def get_bw_peaks(self) -> Dict[str, float]:
        if biprof_ctx.arch == ArchName.V100:
            return MEMORY_BANDWIDTH_V100
        return defaultdict(float)
