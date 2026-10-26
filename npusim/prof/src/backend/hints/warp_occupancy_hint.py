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


class WarpOccupancyHint(IHint):
    # Ported from biprof: fires on VF_SIMT instructions with low warp
    # occupancy (the share of available SIMT warps active in the block,
    # 0-1). WarpOccupancy is computed by the Ascend950WarpOccupancyAnalyzer
    # (requires --object-file) into SIMTWarpOccupancy and is exposed via
    # the AsmMetrics view; it is NULL when not populated.

    @staticmethod
    def get_name() -> str:
        return "v310_warp_occupancy_hint"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        warp_occupancy = instr["WarpOccupancy"]
        return (
            str(instr["InstrName"]).startswith("VF_SIMT") and warp_occupancy is not None and float(warp_occupancy) < 0.5
        )

    def get_instruction_hint_text(self, instr: sqlite3.Row) -> str:
        return (
            f"Low warp occupancy detected "
            f"({round(instr['WarpOccupancy'] * 100, 2)}%). "
            "Consider to use more warps and eliminate job "
            "imbalance for better performance."
        )
