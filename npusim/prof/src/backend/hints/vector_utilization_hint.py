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
from npusim.prof.src.backend.hints.base.hint_base import IHint
from npusim.prof.src.common.biprof_constants import RELTIME_THRESHOLD


class VectorUtilizationHint(IHint):
    @staticmethod
    def get_name() -> str:
        return "vector_utilization_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return (
            float(instr["RelTime"]) >= RELTIME_THRESHOLD
            and instr["VectorUtilization"] is not None
            and float(instr["VectorUtilization"]) <= 50.0
        )

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return (
            "Low vector utilization detected "
            f"({round(instr['VectorUtilization'], 2)}%). "
            "This can be a sign of inefficient "
            "vector instruction usage. "
            "Try to increase the number of processed elements "
            "and/or repeats for this vector instruction."
        )
