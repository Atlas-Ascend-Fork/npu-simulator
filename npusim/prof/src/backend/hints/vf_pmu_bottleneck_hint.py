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
    BOUND_ROOTS,
    MAJOR_BOUND,
    NODE_TRIGGER,
    children_of,
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    short_name,
    subcore_label,
    worst_subcore,
)


class VfPmuBottleneckHint(IHint):
    # Kernel-level hint: the executive summary of the VF PMU top-down
    # hierarchy. Fires when the dominant bound root (frontend_bound,
    # backend_asu_bound, backend_vec_bound) of the worst subcore takes at
    # least MAJOR_BOUND of the optimal slots, then walks down the tree to
    # the main contributing node so the message points at the likely
    # cause. Thresholds mirror the VF PMU bottleneck report.

    # Descend while the best child carries at least this share of its parent.
    CONTRIB_WALK_SHARE = 0.5

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_bottleneck_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def find_main_contributor(self, nodes: Dict[str, float], root: str) -> Tuple[str, float]:
        node = root
        value = nodes.get(root, 0.0)
        while True:
            best_child: Optional[str] = None
            best_value = 0.0
            for child in children_of(node):
                child_value = nodes.get(child)
                if child_value is None:
                    continue
                if best_child is None or child_value > best_value:
                    best_child, best_value = child, child_value
            if best_child is None or best_value < self.CONTRIB_WALK_SHARE * value or best_value < NODE_TRIGGER:
                return node, value
            node, value = best_child, best_value

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        subcore_values = fetch_subcore_values()

        worst: Optional[Tuple[Tuple[int, int], str, float]] = None
        for root in BOUND_ROOTS:
            hit = worst_subcore(subcore_values, root)
            if hit is None:
                continue
            (key, value), root_name = hit, root
            if worst is None or value > worst[2]:
                worst = (key, root_name, value)
        if worst is None or worst[2] < MAJOR_BOUND:
            return self.result

        key, root, root_value = worst
        nodes = subcore_values[key]
        contributor, contrib_value = self.find_main_contributor(nodes, root)
        self.add_hint(
            f"VF PMU dominant bottleneck on {subcore_label(*key)}: "
            f"{short_name(root)} takes {root_value:.1%} of the optimal "
            f"slots; main contributor: {short_name(contributor)} "
            f"{contrib_value:.1%} — {node_advice(contributor)}",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
