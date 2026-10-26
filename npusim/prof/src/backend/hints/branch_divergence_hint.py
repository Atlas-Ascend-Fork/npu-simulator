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
from typing import List

from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.common.biprof_constants import RELTIME_THRESHOLD


class BranchDivergenceHint(IHint):
    # Ported from biprof: flags executed branches whose warp divergence
    # (BranchEfficiency below 90, populated by the BranchEfficiency
    # analyzer) eats a significant share of the kernel time. The logic
    # itself is unchanged; only the imports live in this package now.

    msg = (
        "Inefficient usage of warps detected "
        "(some threads in a warp do nothing due to a conditional). "
        "Consider changing the conditional to utilize all the "
        "threads inside the warp."
    )

    @staticmethod
    def get_name() -> str:
        return "v310_branch_divergence_hint"

    @classmethod
    def get_instruction_hint_text(cls, instr: sqlite3.Row) -> str:
        return cls.msg

    @classmethod
    def get_sourceline_hint_text(cls, sourceline_instrs: List[sqlite3.Row]) -> str:
        return cls.msg

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        branch_efficiency = instr["BranchEfficiency"]
        reltime = instr["RelTime"]
        return (
            reltime is not None
            and float(reltime) >= RELTIME_THRESHOLD
            and branch_efficiency is not None
            and branch_efficiency < 90
        )

    def sourceline_condition(self, sourceline_instrs: List[sqlite3.Row]) -> bool:
        reltime_sum = 0
        for instr in sourceline_instrs:
            branch_efficiency = instr["BranchEfficiency"]
            reltime = instr["RelTime"]
            if reltime is not None and branch_efficiency is not None and branch_efficiency < 90:
                reltime_sum += reltime
        return reltime_sum >= RELTIME_THRESHOLD
