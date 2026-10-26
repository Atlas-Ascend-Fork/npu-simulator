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
    fetch_subcore_values,
    has_vf_pmu_data,
    node_advice,
    subcore_label,
    worst_subcore,
)


class VfPmuExqImbalanceHint(IHint):
    # Kernel-level hint: EXQ0/EXQ1 dual-issue imbalance (one EX queue holds
    # two or more ready instructions while the other is empty). Indicates
    # that independent instructions were scheduled onto one queue.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_exq_imbalance_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        hit = worst_subcore(fetch_subcore_values(), "syn_c_ex_stall_imbalance")
        if hit is None or hit[1] < NODE_TRIGGER:
            return self.result
        key, value = hit
        self.add_hint(
            f"VF PMU EXQ imbalance on {subcore_label(*key)}: "
            f"ex_stall_imbalance {value:.1%} of the optimal slots — "
            f"{node_advice('syn_c_ex_stall_imbalance')}",
            [HintTarget(None, None, None, is_kernel=True)],
        )
        return self.result
