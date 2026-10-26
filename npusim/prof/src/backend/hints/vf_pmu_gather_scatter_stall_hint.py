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
from typing import Dict, List, Optional, Tuple

from npusim.prof.src.backend.hints.base.hint_base import (
    HintDataItem,
    HintTarget,
    IHint,
)
from npusim.prof.src.backend.hints.vf_pmu_hint_utils import (
    GATHER_SCATTER_NODES,
    NODE_TRIGGER,
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    short_name,
    subcore_label,
)


class VfPmuGatherScatterStallHint(IHint):
    # Kernel-level hint: gather/scatter issue stalls (the ga_* children of
    # ld_issue_stall and the sc_* children of st_issue_stall), summed
    # before the comparison. Complements the access_pattern hint, which
    # flags that gathers/scatters run at all; this one quantifies how much
    # of the slot budget they waste.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_gather_scatter_stall_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        subcore_values = fetch_subcore_values()

        worst: Optional[Tuple[Tuple[int, int], float, str, float]] = None
        for key, nodes in subcore_values.items():
            total = sum(nodes.get(node, 0.0) for node in GATHER_SCATTER_NODES)
            if total == 0.0:
                continue
            top_node = max(GATHER_SCATTER_NODES, key=lambda n: nodes.get(n, 0.0))
            top_value = nodes.get(top_node, 0.0)
            if worst is None or total > worst[1]:
                worst = (key, total, top_node, top_value)
        if worst is None or worst[1] < NODE_TRIGGER:
            return self.result

        key, total, top_node, top_value = worst
        self.add_hint(
            f"VF PMU gather/scatter stalls on {subcore_label(*key)}: "
            f"{total:.1%} of the optimal slots (top: "
            f"{short_name(top_node)} {top_value:.1%}) — "
            f"{node_advice(top_node)}",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
