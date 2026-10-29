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

from typing import Dict, List, Optional

from npusim.prof.src.backend.hints.base.hint_base import HintDataItem, HintTarget, IHint
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import TAIL_EFFECT_HINT_THRESHOLD


class TailEffectInterCoreHint(IHint):
    # Compare spans of all active core IDs, combining their subcore types.
    # Span = last instruction end - first instruction start. Absolute start
    # time is ignored; idle gaps count as part of the span.
    # Emit at most one kernel hint for the shortest/longest pair when
    # (longest - shortest) / longest >= 10%; other pairs are not reported.
    # Example: core 0 [0, 100], core 1 [30, 130] -> both 100 -> do not emit.
    #          core 0 [0, 100], core 1 [30, 120] -> 100 vs 90 -> emit.

    @staticmethod
    def get_name() -> str:
        return "tail_effect_inter_core"

    def process(self, instrs_map: Dict[Optional[int], List]) -> List[HintDataItem]:
        spans = database_handler.execute(
            "SELECT ExecInstrCoreId AS CoreId, "
            "MAX(ExecInstrTickEnd) - MIN(ExecInstrTickStart) AS Span "
            "FROM ExecutedInstructions GROUP BY ExecInstrCoreId"
        ).fetchall()
        if len(spans) < 2:
            return self.result
        longest = max(spans, key=lambda row: row["Span"])
        shortest = min(spans, key=lambda row: row["Span"])
        max_span, min_span = int(longest["Span"]), int(shortest["Span"])
        if max_span <= 0:
            return self.result
        gap_ratio = (max_span - min_span) / max_span
        if gap_ratio < TAIL_EFFECT_HINT_THRESHOLD:
            return self.result
        gap = 100 * gap_ratio
        self.add_hint(
            f"Tail effect between cores: core {longest['CoreId']} and "
            f"core {shortest['CoreId']} spans differ by {gap:.1f}% of the "
            "longer span. Consider balancing work across cores.",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
