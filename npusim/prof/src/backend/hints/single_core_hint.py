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

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.hints.base.hint_base import (
    HintDataItem,
    HintTarget,
    IHint,
)


class SingleCoreHint(IHint):
    # Kernel-level hint (ported from biprof "general hints"): fires when
    # ai_core_active <= 1, i.e. the kernel launched on at most one sub-core
    # type. Routes into the KernelHints table via is_kernel=True.
    #
    # Note: this overlaps with the existing SmallGridHint, which uses a
    # different signal (DISTINCT ExecInstrCoreId vs the architecture
    # maximum from core_num_map). Both are registered intentionally.

    @staticmethod
    def get_name() -> str:
        return "general_single_core"

    def instruction_condition(self, instr: sqlite3.Row) -> bool:
        return False

    def process(self, instrs_map: Dict[Optional[int], List[sqlite3.Row]]) -> List[HintDataItem]:
        row = database_handler.execute(
            """
            SELECT KernelInfoVal FROM KernelInfo
            WHERE KernelInfoAttr = 'ai_core_active'
            """
        ).fetchone()
        if row and int(row[0]) <= 1:
            self.add_hint(
                "Single-core / under-launched kernel: only "
                f"{row[0]} core active. Query GetCoreNumAiv() or "
                "GetCoreNumAic() and launch on all cores. "
                "This is the dominant bottleneck — fix first.",
                [HintTarget(None, None, None, is_kernel=True)],
            )
        return self.result
