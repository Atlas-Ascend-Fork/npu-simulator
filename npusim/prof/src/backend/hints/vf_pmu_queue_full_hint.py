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
    NODE_TRIGGER,
    QUEUE_NODES,
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    short_name,
    subcore_label,
    worst_subcore,
)


class VfPmuQueueFullHint(IHint):
    # Kernel-level hint: load/store queue full stalls (the issue_que_stall
    # subtree of the hardware cut). One message per hot queue on the worst
    # subcore — ldq_stall and shq_stall get separate advice.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_queue_full_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        subcore_values = fetch_subcore_values()
        for node in QUEUE_NODES:
            hit = worst_subcore(subcore_values, node)
            if hit is None or hit[1] < NODE_TRIGGER:
                continue
            key, value = hit
            self.add_hint(
                f"VF PMU queue full stall on {subcore_label(*key)}: "
                f"{short_name(node)} {value:.1%} of the optimal slots — "
                f"{node_advice(node)}",
                [HintTarget(None, None, None, is_kernel=True)],
            )
        return self.result
