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
    REG_PRESSURE_NODES,
    VREG_ADVICE,
    VREG_VF_TRIGGER,
    WORST_VF_TOP_N,
    fetch_subcore_values,
    fetch_vf_values,
    group_vf_values_by_subcore,
    has_vf_pmu_data,
    map_vfids_to_marker_pcs,
    node_advice,
    short_name,
    subcore_label,
    worst_subcore,
)


class VfPmuRegPressureHint(IHint):
    # Physical register file pressure, from the hardware cut of the
    # backend_vec_bound top-down node. Kernel-level message when the worst
    # subcore exceeds the advice level (VREG_ADVICE, matching the VF PMU
    # summary report); the worst VFs above VREG_VF_TRIGGER additionally get
    # an instruction hint on their VF marker PC, pinpointing where in the
    # instruction stream the pressure lives.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_reg_pressure_hint"

    def __init__(self) -> None:
        super().__init__()
        self.pc_messages: Dict[int, str] = {}

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return int(instr["PC"]) in self.pc_messages

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return self.pc_messages[int(instr["PC"])]

    def _process_kernel(self, subcore_values: Dict[Tuple[int, int], Dict[str, float]]) -> None:
        for node in REG_PRESSURE_NODES:
            hit = worst_subcore(subcore_values, node)
            if hit is None or hit[1] < VREG_ADVICE:
                continue
            key, value = hit
            self.add_hint(
                f"VF PMU register pressure on {subcore_label(*key)}: "
                f"{short_name(node)} {value:.1%} of the optimal slots — "
                f"{node_advice(node)}",
                [HintTarget(None, None, None, is_kernel=True)],
            )

    def _process_per_vf(self) -> None:
        mapping = map_vfids_to_marker_pcs()
        for key, vfs in group_vf_values_by_subcore(fetch_vf_values()).items():
            candidates = []
            for vf_id, nodes in vfs.items():
                fraction = nodes.get("syn_c_phy_vreg_stall")
                if fraction is None or fraction < VREG_VF_TRIGGER:
                    continue
                optimal = nodes.get("syn_c_optimal", 0.0)
                candidates.append((vf_id, fraction, optimal))
            # Worst first, by absolute wasted slots rather than fraction,
            # so a small but terrible VF cannot outrank a big one.
            candidates.sort(key=lambda item: item[1] * item[2], reverse=True)
            for vf_id, fraction, _ in candidates[:WORST_VF_TOP_N]:
                message = (
                    f"VF PMU register pressure in VF #{vf_id} on "
                    f"{subcore_label(*key)}: phy_vreg_stall {fraction:.1%} "
                    f"of the VF's optimal slots — "
                    f"{node_advice('syn_c_phy_vreg_stall')}"
                )
                pc_map = mapping.get(key)
                if pc_map is not None and vf_id in pc_map:
                    self.pc_messages[pc_map[vf_id]] = message
                else:
                    self.add_hint(message, [HintTarget(None, None, None, is_kernel=True)])

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        self._process_kernel(fetch_subcore_values())
        self._process_per_vf()
        if self.pc_messages:
            super().process(instrs_map)
        return self.result
