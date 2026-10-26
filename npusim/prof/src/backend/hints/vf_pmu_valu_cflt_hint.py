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
    NODE_TRIGGER,
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    subcore_label,
)


class VfPmuValuCfltHint(IHint):
    # Kernel-level hint: VALU issue conflicts — throughput conflicts (two
    # instructions wanting the same dual-issue slot) and resource conflicts
    # (same functional-unit class back to back). Both children of
    # ex_stall_valu_cflt are summed; the message shows the split.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_valu_cflt_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        subcore_values = fetch_subcore_values()

        worst: Optional[Tuple[Tuple[int, int], float, float, float]] = None
        for key, nodes in subcore_values.items():
            throughput = nodes.get("syn_c_valu_cflt_throughput", 0.0)
            resource = nodes.get("syn_c_valu_cflt_resource", 0.0)
            total = throughput + resource
            if total == 0.0:
                continue
            if worst is None or total > worst[1]:
                worst = (key, total, throughput, resource)
        if worst is None or worst[1] < NODE_TRIGGER:
            return self.result

        key, total, throughput, resource = worst
        node = "syn_c_valu_cflt_throughput" if throughput >= resource else "syn_c_valu_cflt_resource"
        self.add_hint(
            f"VF PMU VALU conflicts on {subcore_label(*key)}: {total:.1%} "
            f"of the optimal slots (throughput {throughput:.1%}, "
            f"resource {resource:.1%}) — {node_advice(node)}",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
