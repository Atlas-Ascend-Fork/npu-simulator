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

from npusim.prof.src.backend.hints.base.hint_base import (
    HintDataItem,
    HintTarget,
    IHint,
)
from npusim.prof.src.backend.hints.vf_pmu_hint_utils import (
    UB_BW_NODE,
    UB_BW_TRIGGER,
    UB_PORT_NODES,
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    short_name,
    subcore_label,
    worst_subcore,
)


class VfPmuUbBwHint(IHint):
    # Kernel-level hint: unified bandwidth (UB) saturation of the LSU port,
    # measured as effective bytes per busy cycle over the 512B/cycle peak.
    # The message carries the per-port breakdown (ldu/stu/gsu) so the user
    # sees which side saturates.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_ub_bw_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        subcore_values = fetch_subcore_values()
        hit = worst_subcore(subcore_values, UB_BW_NODE)
        if hit is None or hit[1] < UB_BW_TRIGGER:
            return self.result
        key, _ = hit
        nodes = subcore_values[key]
        breakdown = ", ".join(f"{short_name(node)} {nodes.get(node, 0.0):.0f}B/cycle" for node in UB_PORT_NODES)
        self.add_hint(
            f"VF PMU UB bandwidth saturation on {subcore_label(*key)}: "
            f"ub_bw_lsu at {nodes.get(UB_BW_NODE, 0.0):.0%} of peak "
            f"({breakdown}) — {node_advice(UB_BW_NODE)}",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
