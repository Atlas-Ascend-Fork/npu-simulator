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
from typing import Iterator, Optional

from npusim.prof.src.backend.analyzers.base.vector_utilization_analyzer_base import (
    VecUtilEntry,
    VectorUtilizationAnalyzerBase,
)
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import DEFAULT_VL_VALUE
from npusim.prof.src.common.extend_params import ExtendParams


class VectorUtilizationAnalyzer(VectorUtilizationAnalyzerBase):
    supported_calc_instrs = {
        "RV_VABS",
        "RV_VADD",
        "RV_VADD3",
        "RV_VADDC",
        "RV_VADDS",
        "RV_VADIF",
        "RV_VAND",
        "RV_VAVG",
        "RV_VAXPY",
        "RV_VBCNT",
        "RV_VCADD",
        "RV_VCLS",
        "RV_VCMAX",
        "RV_VCMIN",
        "RV_VCMP",
        "RV_VCMPS",
        "RV_VDIV",
        "RV_VDUP",
        "RV_VDUPS",
        "RV_VEXP",
        "RV_VEXP2",
        "RV_VEXPDIF",  # limited support
        "RV_VGATHER2",
        "RV_VLN",
        "RV_VLRELU",
        "RV_VMADD",
        "RV_VMAX",
        "RV_VMAXRELU",
        "RV_VMAXS",
        "RV_VMIN",
        "RV_VMINRELU",
        "RV_VMINS",
        "RV_VMUL",
        "RV_VMULA",
        "RV_VMULS",
        "RV_VNEG",
        "RV_VNOT",
        "RV_VOR",
        "RV_VPRELU",
        "RV_VREC",
        "RV_VRELU",
        "RV_VRND",
        "RV_VRNDI",
        "RV_VRNDS",
        "RV_VRSQRT",
        "RV_VSAD",
        "RV_VSADD",
        "RV_VSADDS",
        "RV_VSCATTER",
        "RV_VSHL",
        "RV_VSHLI",
        "RV_VSHLS",
        "RV_VSHR",
        "RV_VSHRI",
        "RV_VSHRS",
        "RV_VSQRT",
        "RV_VSSUB",
        "RV_VSUB",
        "RV_VSUBC",
        "RV_VSUBCS",
        "RV_VXOR",
    }

    supported_ldst_instrs = {
        "RV_VLD",
        "RV_VLDI",
        "RV_VLDS",
        "RV_VST",
        "RV_VSTI",
        "RV_VSTS",
    }

    @classmethod
    def iter_vec_instrs(cls) -> Iterator[sqlite3.Row]:
        return database_handler.iter_rows(
            """
            SELECT ExecutedInstructions.ExecInstrId,
                   ExecutedInstructions.ExecInstrName,
                   ExecutedInstructions.ExtendParams
            FROM ExecutedInstructions
            WHERE ExecutedInstructions.ExecInstrName LIKE 'RV_V%'
        """
        )

    @classmethod
    def is_supported_instr(cls, instr: sqlite3.Row) -> bool:
        return instr["ExecInstrName"] in (cls.supported_calc_instrs | cls.supported_ldst_instrs)

    def calc_utilization(self, instr: sqlite3.Row) -> Optional[VecUtilEntry]:
        # With the contract DB, processed_bytes is provided directly in
        # ExtendParams.  Vector utilization = processed_bytes / DEFAULT_VL.
        ep = ExtendParams.parse(instr["ExtendParams"])
        processed_bytes = ep.processed_bytes
        if processed_bytes is None:
            return None
        instr_id = instr["ExecInstrId"]
        vec_util_value = processed_bytes / DEFAULT_VL_VALUE * 100.0
        return VecUtilEntry(instr_id, processed_bytes, None, vec_util_value)
