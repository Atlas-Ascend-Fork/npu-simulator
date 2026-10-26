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
    compute_overlaps,
    overlaps_to_string,
)


class LowVecMte2OverlapHint(IHint):
    # Kernel-level hint: fires when the overlap between the VEC pipe
    # (PUSHQ-based, remapped to "VEC" by overlap_utils) and the MTE2 pipe
    # on AIV cores is low, indicating that data movement (MTE2) and vector
    # compute (VEC) run serially with no/inefficient double-buffering.
    #
    # Distinct from AivUnderutilizationHint, which requires low SIMD/SIMT
    # utilization in addition to low overlap. This hint fires on low
    # overlap alone (when both pipes have meaningful work), so it also
    # catches high-utilization kernels whose pipes do not overlap.
    #
    # Routes into the KernelHints table via is_kernel=True.

    OVERLAP_THRESHOLD = 0.20
    MIN_INSTR_COUNT = 4

    VEC_MTE2_PAIRS = [
        (("AIV0", "MTE2"), ("AIV0", "VEC"), "AIV0_MTE2_vs_AIV0_VEC"),
        (("AIV1", "MTE2"), ("AIV1", "VEC"), "AIV1_MTE2_vs_AIV1_VEC"),
    ]

    @staticmethod
    def get_name() -> str:
        return "low_vec_mte2_overlap_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        # Guard: require meaningful work on both VEC and MTE2 pipes on at
        # least one AIV core, otherwise low overlap is not informative.
        # PUSHQ is the VEC pipe (InstrType name PUSHQ -> "VEC" via
        # overlap_utils), MTE2 is InstrTypeId 4.
        cursor = database_handler.execute(
            """
            SELECT CoreTypeId, InstrTypeId, COUNT(*) AS n
            FROM ExecutedInstructions
            WHERE InstrTypeId IN (
                SELECT InstrTypeId FROM InstrTypes
                WHERE InstrTypeName IN ('PUSHQ', 'MTE2')
            )
            AND CoreTypeId IN (
                SELECT CoreTypeId FROM CoreTypes
                WHERE CoreTypeName IN ('AIV0', 'AIV1')
            )
            GROUP BY CoreTypeId, InstrTypeId
            """
        )
        has_vec = False
        has_mte2 = False
        for row in cursor.fetchall():
            instr_type_id = row[1]
            count = int(row[2])
            if instr_type_id == 55 and count >= self.MIN_INSTR_COUNT:
                # PUSHQ (InstrTypeId 55) -> VEC pipe
                has_vec = True
            elif instr_type_id == 4 and count >= self.MIN_INSTR_COUNT:
                # MTE2 (InstrTypeId 4)
                has_mte2 = True
        if not (has_vec and has_mte2):
            return self.result

        overlaps = compute_overlaps(self.VEC_MTE2_PAIRS)

        # Keep only AIV MTE2-vs-VEC overlaps that are not None (both pipes
        # populated on that core).
        relevant = {
            label: overlaps[label] for label in (p[2] for p in self.VEC_MTE2_PAIRS) if overlaps.get(label) is not None
        }
        if not relevant:
            return self.result

        if not all(v < self.OVERLAP_THRESHOLD for v in relevant.values()):
            return self.result

        overlap_str = overlaps_to_string(overlaps, lambda k: k in relevant)
        self.add_hint(
            f"Low VEC/MTE2 overlap ({overlap_str}): all overlaps < "
            f"{self.OVERLAP_THRESHOLD}. VEC and MTE2 pipes run serially "
            "with no/inefficient double-buffering. Consider interleaving "
            "MTE2 data-movement with VEC compute to overlap them.",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
