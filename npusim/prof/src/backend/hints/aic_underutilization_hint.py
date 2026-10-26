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


class AicUnderutilizationHint(IHint):
    # Kernel-level hint (ported from biprof "general hints"): fires when
    # the AIC CUBE pipe mean utilization is very low AND none of the AIC
    # pipeline overlaps with CUBE exceed a small threshold, indicating the
    # kernel barely does compute work with no pipeline overlap. Routes into
    # the KernelHints table via is_kernel=True.
    #
    # Compatible with npusim.prof data: the AIC pipe pairs (CUBE/MTE1/MTE2/
    # FIXP/SCALAR) all map to existing InstrQueueType / InstrType values,
    # and PipesUtilization is populated for these queue types.

    UTIL_THRESHOLD = 0.10
    OVERLAP_THRESHOLD = 0.10

    AIC_PIPE_PAIRS = [
        (("AIC", "MTE2"), ("AIC", "CUBE"), "AIC_MTE2_vs_AIC_CUBE"),
        (("AIC", "MTE1"), ("AIC", "CUBE"), "AIC_MTE1_vs_AIC_CUBE"),
        (("AIC", "FIXP"), ("AIC", "CUBE"), "AIC_FIXP_vs_AIC_CUBE"),
        (("AIC", "SCALAR"), ("AIC", "CUBE"), "AIC_SCALAR_vs_AIC_CUBE"),
    ]

    @staticmethod
    def get_name() -> str:
        return "general_aic_underutilization"

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
        pipe_values: List[float] = []
        for row in cursor.fetchall():
            core_type_id = row[1]
            pipe_type_id = row[2]
            util = row[3]
            core_type = CoreType(core_type_id).name
            pipe_type = InstrQueueType(pipe_type_id).name
            if core_type == "AIC" and pipe_type == "CUBE":
                pipe_values.append(util)
        if not pipe_values:
            return self.result
        mean_util = sum(pipe_values) / len(pipe_values) / 100
        mean_util = round(mean_util, 4)
        if mean_util >= self.UTIL_THRESHOLD:
            return self.result

        overlaps = compute_overlaps(self.AIC_PIPE_PAIRS)

        def aic_filter(k: str) -> bool:
            return k.startswith("AIC_") and k.endswith("_vs_AIC_CUBE")

        if not all_overlaps_below(overlaps, self.OVERLAP_THRESHOLD, aic_filter):
            return self.result

        overlap_str = overlaps_to_string(overlaps, aic_filter)
        self.add_hint(
            f"AIC underutilization: AIC_CUBE.mean={mean_util} "
            f"(threshold {self.UTIL_THRESHOLD}), all overlaps < "
            f"{self.OVERLAP_THRESHOLD} ({overlap_str}). The kernel barely "
            "does compute work with no pipeline overlap. Consider shrinking "
            "blockDim to match the work, using a representative shape, "
            "or shrinking baseM/baseN to manufacture more tiles.",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
