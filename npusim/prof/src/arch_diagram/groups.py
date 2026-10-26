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

from npusim.prof.src.arch_diagram.metric_group import MetricGroup
from npusim.prof.src.arch_diagram.metric import Metric
from enum import Enum


class MetricGroups(Enum):
    SIM_MEMORY_TPUT_CUBE_GBS = MetricGroup(
        "SimMemoryThroughputCubeGbs",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aic_out_to_l1_gbs", "tp"),
            Metric("aic_l1_to_l0a_gbs", "tp"),
            Metric("aic_l1_to_l0b_gbs", "tp"),
            Metric("aic_l0a_to_cube_gbs", "tp"),
            Metric("aic_l0b_to_cube_gbs", "tp"),
            Metric("aic_cube_to_l0c_gbs", "tp"),
            Metric("aic_l0c_to_out_gbs", "tp"),
            Metric("aic_l0c_to_l1_gbs", "tp"),
            Metric("aic_l0c_to_ub0_gbs", "tp"),
            Metric("aic_l0c_to_ub1_gbs", "tp"),
            Metric("aic_l0c_to_cube_gbs", "tp"),
            Metric("aic_l0c_to_all_syn_gbs", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_CUBE_CNT = MetricGroup(
        "SimMemoryThroughputCubeCnt",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aic_out_to_l1_cnt", "count"),
            Metric("aic_l1_to_l0a_cnt", "count"),
            Metric("aic_l1_to_l0b_cnt", "count"),
            Metric("aic_l0a_to_cube_cnt", "count"),
            Metric("aic_l0b_to_cube_cnt", "count"),
            Metric("aic_cube_to_l0c_cnt", "count"),
            Metric("aic_l0c_to_out_cnt", "count"),
            Metric("aic_l0c_to_l1_cnt", "count"),
            Metric("aic_l0c_to_ub0_cnt", "count"),
            Metric("aic_l0c_to_ub1_cnt", "count"),
            Metric("aic_l0c_to_cube_cnt", "count"),
            Metric("aic_l0c_to_all_syn_cnt", "count"),
        ),
    )
    SIM_MEMORY_TPUT_CUBE_RATIO = MetricGroup(
        "SimMemoryThroughputCubeRatio",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aic_out_to_l1_ratio", "tp"),
            Metric("aic_l1_to_l0a_ratio", "tp"),
            Metric("aic_l1_to_l0b_ratio", "tp"),
            Metric("aic_l0a_to_cube_ratio", "tp"),
            Metric("aic_l0b_to_cube_ratio", "tp"),
            Metric("aic_cube_to_l0c_ratio", "tp"),
            Metric("aic_l0c_to_out_ratio", "tp"),
            Metric("aic_l0c_to_l1_ratio", "tp"),
            Metric("aic_l0c_to_ub0_ratio", "tp"),
            Metric("aic_l0c_to_ub1_ratio", "tp"),
            Metric("aic_l0c_to_cube_ratio", "tp"),
            Metric("aic_l0c_to_all_syn_ratio", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_VEC0_GBS = MetricGroup(
        "SimMemoryThroughputVector0Gbs",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv0_ub_to_simd_gbs", "tp"),
            Metric("aiv0_simd_to_ub_gbs", "tp"),
            Metric("aiv0_ub_to_simt_gbs", "tp"),
            Metric("aiv0_simt_to_ub_gbs", "tp"),
            Metric("aiv0_out_to_ub_gbs", "tp"),
            Metric("aiv0_ub_to_out_gbs", "tp"),
            Metric("aiv0_ub_to_l1_gbs", "tp"),
            Metric("aiv0_out_to_simt_gbs", "tp"),
            Metric("aiv0_simt_to_out_gbs", "tp"),
            Metric("aiv0_cache_to_simt_gbs", "tp"),
            Metric("aiv0_simt_to_cache_gbs", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_VEC0_CNT = MetricGroup(
        "SimMemoryThroughputVector0Cnt",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv0_ub_to_simd_cnt", "count"),
            Metric("aiv0_simd_to_ub_cnt", "count"),
            Metric("aiv0_ub_to_simt_cnt", "count"),
            Metric("aiv0_simt_to_ub_cnt", "count"),
            Metric("aiv0_out_to_ub_cnt", "count"),
            Metric("aiv0_ub_to_out_cnt", "count"),
            Metric("aiv0_ub_to_l1_cnt", "count"),
            Metric("aiv0_out_to_simt_cnt", "count"),
            Metric("aiv0_simt_to_out_cnt", "count"),
            Metric("aiv0_cache_to_simt_cnt", "count"),
            Metric("aiv0_simt_to_cache_cnt", "count"),
        ),
    )
    SIM_MEMORY_TPUT_VEC0_RATIO = MetricGroup(
        "SimMemoryThroughputVector0Ratio",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv0_ub_to_simd_ratio", "tp"),
            Metric("aiv0_simd_to_ub_ratio", "tp"),
            Metric("aiv0_ub_to_simt_ratio", "tp"),
            Metric("aiv0_simt_to_ub_ratio", "tp"),
            Metric("aiv0_out_to_ub_ratio", "tp"),
            Metric("aiv0_ub_to_out_ratio", "tp"),
            Metric("aiv0_ub_to_l1_ratio", "tp"),
            Metric("aiv0_out_to_simt_ratio", "tp"),
            Metric("aiv0_simt_to_out_ratio", "tp"),
            Metric("aiv0_cache_to_simt_ratio", "tp"),
            Metric("aiv0_simt_to_cache_ratio", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_VEC1_GBS = MetricGroup(
        "SimMemoryThroughputVector1Gbs",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv1_ub_to_simd_gbs", "tp"),
            Metric("aiv1_simd_to_ub_gbs", "tp"),
            Metric("aiv1_ub_to_simt_gbs", "tp"),
            Metric("aiv1_simt_to_ub_gbs", "tp"),
            Metric("aiv1_out_to_ub_gbs", "tp"),
            Metric("aiv1_ub_to_out_gbs", "tp"),
            Metric("aiv1_ub_to_l1_gbs", "tp"),
            Metric("aiv1_out_to_simt_gbs", "tp"),
            Metric("aiv1_simt_to_out_gbs", "tp"),
            Metric("aiv1_cache_to_simt_gbs", "tp"),
            Metric("aiv1_simt_to_cache_gbs", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_VEC1_CNT = MetricGroup(
        "SimMemoryThroughputVector1Cnt",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv1_ub_to_simd_cnt", "count"),
            Metric("aiv1_simd_to_ub_cnt", "count"),
            Metric("aiv1_ub_to_simt_cnt", "count"),
            Metric("aiv1_simt_to_ub_cnt", "count"),
            Metric("aiv1_out_to_ub_cnt", "count"),
            Metric("aiv1_ub_to_out_cnt", "count"),
            Metric("aiv1_ub_to_l1_cnt", "count"),
            Metric("aiv1_out_to_simt_cnt", "count"),
            Metric("aiv1_simt_to_out_cnt", "count"),
            Metric("aiv1_cache_to_simt_cnt", "count"),
            Metric("aiv1_simt_to_cache_cnt", "count"),
        ),
    )
    SIM_MEMORY_TPUT_VEC1_RATIO = MetricGroup(
        "SimMemoryThroughputVector1Ratio",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv1_ub_to_simd_ratio", "tp"),
            Metric("aiv1_simd_to_ub_ratio", "tp"),
            Metric("aiv1_ub_to_simt_ratio", "tp"),
            Metric("aiv1_simt_to_ub_ratio", "tp"),
            Metric("aiv1_out_to_ub_ratio", "tp"),
            Metric("aiv1_ub_to_out_ratio", "tp"),
            Metric("aiv1_ub_to_l1_ratio", "tp"),
            Metric("aiv1_out_to_simt_ratio", "tp"),
            Metric("aiv1_simt_to_out_ratio", "tp"),
            Metric("aiv1_cache_to_simt_ratio", "tp"),
            Metric("aiv1_simt_to_cache_ratio", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_HBM_GBS = MetricGroup(
        "SimMemoryThroughputHbmGbs",
        "",
        "ArchDiagramMetrics",
        (
            Metric("hbm_to_l2_syn_gbs", "tp"),
            Metric("l2_to_hbm_syn_gbs", "tp"),
        ),
    )
    SIM_MEMORY_TPUT_HBM_CNT = MetricGroup(
        "SimMemoryThroughputHbmCnt",
        "",
        "ArchDiagramMetrics",
        (
            Metric("hbm_to_l2_syn_cnt", "count"),
            Metric("l2_to_hbm_syn_cnt", "count"),
        ),
    )
    SIM_MEMORY_TPUT_HBM_RATIO = MetricGroup(
        "SimMemoryThroughputHbmRatio",
        "",
        "ArchDiagramMetrics",
        (
            Metric("hbm_to_l2_syn_ratio", "tp"),
            Metric("l2_to_hbm_syn_ratio", "tp"),
        ),
    )
    SIM_PIPE_UTIL_CUBE = MetricGroup(
        "SimPipeUtilizationCube",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aic_cube_ratio", "usage"),
            Metric("aic_fixp_ratio", "usage"),
            Metric("aic_scalar_ratio", "usage"),
        ),
    )
    SIM_PIPE_UTIL_VEC0 = MetricGroup(
        "SimPipeUtilizationVector0",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv0_simd_ratio", "usage"),
            Metric("aiv0_simt_ratio", "usage"),
            Metric("aiv0_scalar_ratio", "usage"),
        ),
    )
    SIM_PIPE_UTIL_VEC1 = MetricGroup(
        "SimPipeUtilizationVector1",
        "",
        "ArchDiagramMetrics",
        (
            Metric("aiv1_simd_ratio", "usage"),
            Metric("aiv1_simt_ratio", "usage"),
            Metric("aiv1_scalar_ratio", "usage"),
        ),
    )
    SIM_L2_CACHE_USAGE = MetricGroup(
        "SimL2CacheUsage",
        "",
        "ArchDiagramMetrics",
        (Metric("l2_cached_ratio", "usage"),),
    )
