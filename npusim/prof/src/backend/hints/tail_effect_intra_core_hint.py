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

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from npusim.prof.src.backend.hints.base.hint_base import HintDataItem, HintTarget, IHint
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import TAIL_EFFECT_HINT_THRESHOLD
from npusim.prof.src.common.biprof_enum import CoreType


class TailEffectIntraCoreHint(IHint):
    # For each core, compare the shortest and longest spans of its active
    # subcore types. Span = last instruction end - first instruction start;
    # Absolute start time is ignored; idle gaps count as part of the span.
    # Emit at most one hint per core when (longest - shortest) / longest >= 10%.
    # Example: AIC [0, 100], AIV0 [20, 90] -> spans 100 and 70 -> emit.
    #          AIC [0, 100], AIV0 [30, 130] -> both 100 -> do not emit.

    @staticmethod
    def get_name() -> str:
        return "tail_effect_intra_core"

    def process(self, instrs_map: Dict[Optional[int], List]) -> List[HintDataItem]:
        rows = database_handler.execute(
            "SELECT ExecInstrCoreId AS CoreId, CoreTypeId, "
            "MAX(ExecInstrTickEnd) - MIN(ExecInstrTickStart) AS Span "
            "FROM ExecutedInstructions GROUP BY ExecInstrCoreId, CoreTypeId"
        ).fetchall()
        subcore_spans: Dict[int, List[Tuple[int, int]]] = defaultdict(list)
        for row in rows:
            subcore_spans[int(row["CoreId"])].append((int(row["Span"]), int(row["CoreTypeId"])))
        for core_id, spans in sorted(subcore_spans.items()):
            if len(spans) < 2:
                continue
            longest = max(spans, key=lambda item: item[0])
            shortest = min(spans, key=lambda item: item[0])
            max_span, min_span = longest[0], shortest[0]
            gap_ratio = (max_span - min_span) / max_span if max_span > 0 else 0
            if gap_ratio < TAIL_EFFECT_HINT_THRESHOLD:
                continue
            gap = 100 * gap_ratio
            longest_type = CoreType(longest[1]).name
            shortest_type = CoreType(shortest[1]).name
            self.add_hint(
                f"Tail effect within core {core_id}: "
                f"{longest_type} and {shortest_type} spans differ by "
                f"{gap:.1f}% of the longer span. "
                "Consider balancing work between the active subcores.",
                [HintTarget(None, None, None, is_kernel=True)],
            )
        return self.result
