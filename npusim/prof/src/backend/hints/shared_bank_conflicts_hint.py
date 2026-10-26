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


class SharedBankConflictsHint(IHint):
    @staticmethod
    def get_name() -> str:
        return "shared_bank_conflicts"

    def sourceline_condition(self, sourceline_instrs: List[sqlite3.Row]) -> bool:
        reltime_sum = 0
        for instr in sourceline_instrs:
            if instr["UBBankConflicts"]:
                reltime_sum += instr["RelTime"]
        return reltime_sum >= RELTIME_THRESHOLD

    @classmethod
    def get_sourceline_hint_text(cls, sourceline_instrs: List[sqlite3.Row]) -> str:
        return (
            "UB bank conflicts detected. "
            "Try to optimize local memory accesses, "
            "prefer sequential patterns instead of strided."
        )
