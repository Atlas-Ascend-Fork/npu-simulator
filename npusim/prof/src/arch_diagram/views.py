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

from npusim.prof.src.arch_diagram.view import ViewData
from npusim.prof.src.arch_diagram.metric_group import (
    ArchDiagramMetricGroup as ADMetGroup,
)
from npusim.prof.src.arch_diagram.groups import MetricGroups
from enum import Enum


class Views(Enum):
    AICORE_MEMORY_SIM_GBS_2_VEC_CORES = ViewData(
        "aicore_memory_sim",
        "gbs",
        (
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_CUBE_GBS.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC0_GBS.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC1_GBS.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_HBM_GBS.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_CUBE.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC0.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC1.value),
            ADMetGroup(MetricGroups.SIM_L2_CACHE_USAGE.value),
        ),
    )
    AICORE_MEMORY_SIM_CNT_2_VEC_CORES = ViewData(
        "aicore_memory_sim",
        "cnt",
        (
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_CUBE_CNT.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC0_CNT.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC1_CNT.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_HBM_CNT.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_CUBE.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC0.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC1.value),
            ADMetGroup(MetricGroups.SIM_L2_CACHE_USAGE.value),
        ),
    )
    AICORE_MEMORY_SIM_RATIO_2_VEC_CORES = ViewData(
        "aicore_memory_sim",
        "ratio",
        (
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_CUBE_RATIO.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC0_RATIO.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_VEC1_RATIO.value),
            ADMetGroup(MetricGroups.SIM_MEMORY_TPUT_HBM_RATIO.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_CUBE.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC0.value),
            ADMetGroup(MetricGroups.SIM_PIPE_UTIL_VEC1.value),
            ADMetGroup(MetricGroups.SIM_L2_CACHE_USAGE.value),
        ),
    )
