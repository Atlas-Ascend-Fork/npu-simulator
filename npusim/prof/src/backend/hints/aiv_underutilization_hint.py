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

import sqlite3
from typing import Dict, List, Optional

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.hints.base.hint_base import (
    HintDataItem,
    HintTarget,
    IHint,
)
from npusim.prof.src.backend.hints.overlap_utils import (
    all_overlaps_below,
    compute_overlaps,
    get_ai_core_active,
    overlaps_to_string,
)
from npusim.prof.src.common.biprof_enum import CoreType, InstrQueueType


class AivUnderutilizationHint(IHint):
    # Kernel-level hint (ported from biprof "general hints"): fires when
    # the AIV SIMD/SIMT pipe mean utilization is very low AND none of the
    # AIV pipeline overlaps with VEC exceed a small threshold. Routes into
    # the KernelHints table via is_kernel=True.

    UTIL_THRESHOLD = 0.05
    OVERLAP_THRESHOLD = 0.10

    AIV_PIPE_PAIRS = [
        (("AIV0", "MTE2"), ("AIV0", "VEC"), "AIV0_MTE2_vs_AIV0_VEC"),
        (("AIV0", "SCALAR"), ("AIV0", "VEC"), "AIV0_SCALAR_vs_AIV0_VEC"),
        (("AIV0", "MTE3"), ("AIV0", "VEC"), "AIV0_MTE3_vs_AIV0_VEC"),
        (("AIV1", "MTE2"), ("AIV1", "VEC"), "AIV1_MTE2_vs_AIV1_VEC"),
        (("AIV1", "SCALAR"), ("AIV1", "VEC"), "AIV1_SCALAR_vs_AIV1_VEC"),
        (("AIV1", "MTE3"), ("AIV1", "VEC"), "AIV1_MTE3_vs_AIV1_VEC"),
    ]

    @staticmethod
    def get_name() -> str:
        return "general_aiv_underutilization"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        ai_core_active = get_ai_core_active()
        if ai_core_active <= 1:
            return self.result

        cursor = database_handler.execute(
            """
            SELECT CoreId, CoreTypeId, InstrQueueTypeId, PipeUtilization
            FROM PipesUtilization
            """
        )
        simd_values: List[float] = []
        simt_values: List[float] = []
        for row in cursor.fetchall():
            core_type_id = row[1]
            pipe_type_id = row[2]
            util = row[3]
            core_type = CoreType(core_type_id).name
            pipe_type = InstrQueueType(pipe_type_id).name
            if core_type.startswith("AIV") and pipe_type == "SIMD":
                simd_values.append(util)
            if core_type.startswith("AIV") and pipe_type == "SIMT":
                simt_values.append(util)
        if not simd_values and not simt_values:
            return self.result
        simd_mean = round(sum(simd_values) / len(simd_values) / 100, 4) if simd_values else 0
        simt_mean = round(sum(simt_values) / len(simt_values) / 100, 4) if simt_values else 0
        if simd_mean >= self.UTIL_THRESHOLD or simt_mean >= self.UTIL_THRESHOLD:
            return self.result

        overlaps = compute_overlaps(self.AIV_PIPE_PAIRS)

        def aiv_filter(k: str) -> bool:
            return k.startswith("AIV")

        if not all_overlaps_below(overlaps, self.OVERLAP_THRESHOLD, aiv_filter):
            return self.result

        overlap_str = overlaps_to_string(overlaps, aiv_filter)
        self.add_hint(
            f"AIV underutilization: AIVx_SIMD.mean={simd_mean}, "
            f"AIVx_SIMT.mean={simt_mean} (threshold "
            f"{self.UTIL_THRESHOLD}), all overlaps < "
            f"{self.OVERLAP_THRESHOLD} ({overlap_str}). The kernel barely "
            "does compute work with no pipeline overlap. Consider shrinking "
            "blockDim to match the work, using a representative shape, "
            "or shrinking tile size to manufacture more tiles.",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
