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
    WORST_VF_MIN_SHARE,
    WORST_VF_RATIO,
    WORST_VF_TOP_N,
    dominant_stall_node,
    fetch_vf_values,
    group_vf_values_by_subcore,
    has_vf_pmu_data,
    map_vfids_to_marker_pcs,
    short_name,
    subcore_label,
)


class VfPmuWorstVfHint(IHint):
    # Per-VF hint: flags VF fragments that retire far fewer slots than
    # their subcore's weighted average (the average is weighted by each
    # VF's optimal slots, and VFs under WORST_VF_MIN_SHARE of the subcore
    # total are skipped as noise). Each bad VF gets an instruction hint on
    # its VF marker PC naming its dominant stall, pinpointing where in the
    # instruction stream the time goes; without a validated marker
    # mapping the finding degrades to a kernel-level message.

    @staticmethod
    def get_name() -> str:
        return "vf_pmu_worst_vf_hint"

    def __init__(self) -> None:
        super().__init__()
        self.pc_messages: Dict[int, str] = {}

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return int(instr["PC"]) in self.pc_messages

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return self.pc_messages[int(instr["PC"])]

    def _find_outliers(self, vfs: Dict[int, Dict[str, float]]) -> List[Tuple[int, float, float, Dict[str, float]]]:
        total_optimal = sum(nodes.get("syn_c_optimal", 0.0) for nodes in vfs.values())
        weight_sum = sum(nodes.get("syn_c_optimal", 0.0) for nodes in vfs.values() if "syn_c_retired" in nodes)
        if total_optimal <= 0.0 or weight_sum <= 0.0:
            return []
        average = (
            sum(
                nodes["syn_c_retired"] * nodes.get("syn_c_optimal", 0.0)
                for nodes in vfs.values()
                if "syn_c_retired" in nodes
            )
            / weight_sum
        )

        outliers = []
        for vf_id, nodes in vfs.items():
            retired = nodes.get("syn_c_retired")
            if retired is None or retired >= WORST_VF_RATIO * average:
                continue
            if nodes.get("syn_c_optimal", 0.0) < WORST_VF_MIN_SHARE * total_optimal:
                continue
            outliers.append((vf_id, retired, average, nodes))
        outliers.sort(key=lambda item: item[1])
        return outliers[:WORST_VF_TOP_N]

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        if not has_vf_pmu_data():
            return self.result
        mapping = map_vfids_to_marker_pcs()
        for key, vfs in group_vf_values_by_subcore(fetch_vf_values()).items():
            for vf_id, retired, average, nodes in self._find_outliers(vfs):
                dominant = dominant_stall_node(nodes)
                dominant_text = (
                    f", dominant stall {short_name(dominant[0])} {dominant[1]:.1%}" if dominant is not None else ""
                )
                message = (
                    f"VF PMU outlier VF #{vf_id} on {subcore_label(*key)}: "
                    f"only {retired:.1%} of the optimal slots retired "
                    f"(subcore average {average:.1%}){dominant_text} — "
                    f"see the SIMD per-VF performance report"
                )
                pc_map = mapping.get(key)
                if pc_map is not None and vf_id in pc_map:
                    self.pc_messages[pc_map[vf_id]] = message
                else:
                    self.add_hint(message, [HintTarget(None, None, None, is_kernel=True)])
        if self.pc_messages:
            super().process(instrs_map)
        return self.result
