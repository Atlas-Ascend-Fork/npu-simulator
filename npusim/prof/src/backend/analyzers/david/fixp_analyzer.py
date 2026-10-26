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
from dataclasses import dataclass
from typing import List, Optional

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams

AIV0_UB_MASK = 1 << 0
AIV1_UB_MASK = 1 << 1


@dataclass
class FixpParams:
    n: int
    m: int
    l2_cache_ctrl_mode: Optional[str]
    hw_checks_unit_flag: bool
    hw_sets_unit_flag: bool
    pre_quant_mode: str
    pre_relu_mode: str
    pre_clip_relu_mode: str
    f32_channel_split_enable: bool
    post_clip_relu_mode: str
    post_relu_mode: str
    nz2nd_enable: bool
    nz2dn_enable: bool
    c0_padding_enable: Optional[bool]
    dst_mode: Optional[str]
    dst_ub_mask: Optional[int]
    processed_bytes: int


pre_quant_modes = {
    0b000000: "No conversion",
    0b000001: "F322F16 (round to the nearest, tie to even)",
    0b000010: "QF322HIF8_PRE (Vector Mode with rounding half to away, F32 -> HiF8)",
    0b000011: "QF322HIF8_PRE (Scalar Mode with rounding half to away, F32 -> HiF8)",
    0b000100: "QF322HIF8_PRE (Vector mode with hybrid rounding, F32 -> HiF8)",
    0b000101: "QF322HIF8_PRE (Scalar mode with hybrid rounding, F32 -> HiF8)",
    0b000110: "DEQS32_INT (Vector mode, S32 -> S32)",
    0b000111: "DEQS32_INT (Scalar mode, S32 -> S32)",
    0b001000: "REQ8 or REQ8_INT (Vector mode, S32 -> S8/U8)",
    0b001001: "REQ8 or REQ8_INT (Scalar mode, S32 -> S8/U8)",
    0b001010: "DEQF16 (Vector mode, S32 -> F16)",
    0b001011: "DEQF16 (Scalar mode, S32 -> F16)",
    0b001100: "QF322FP8_PRE (Vector mode, F32 -> FP8_E4M3",
    0b001101: "QF322FP8_PRE (Scalar mode, F32 -> FP8_E4M3",
    0b001110: "QF322F32_PRE (Vector mode, F32 -> F32)",
    0b001111: "QF322F32_PRE (Scalar mode, F32 -> F32)",
    0b010000: "F322BF16 (round to the nearest, tie to even)",
    0b010101: "REQ4 or REQ4_INT (Vector mode, S32 -> S4)",
    0b010110: "REQ4 or REQ4_INT (Scalar mode, S32 -> S4)",
    0b010111: "QF322B8_PRE (Vector mode, F32 -> S8/U8)",
    0b011000: "QF322B8_PRE (Scalar mode, F32 -> S8/U8)",
    0b011001: "QF322S4_PRE (Vector mode, F32 -> S4)",
    0b011010: "QF322S4_PRE (Scalar mode, F32 -> S4)",
    0b011011: "DEQS16 or DEQS16_INT (Vector mode, S32 -> S16)",
    0b011100: "DEQS16 or DEQS16_INT (Scalar mode, S32 -> S16)",
    0b011111: "QF322F16_PRE (Vector mode, F32 -> F16)",
    0b100000: "QF322F16_PRE (Scalar mode, F32 -> F16)",
    0b100001: "QF322BF16_PRE (Vector mode, F32 -> BF16)",
    0b100010: "QF322BF16_PRE (Scalar mode, F32 -> BF16)",
    0b100011: "QS322BF16_PRE (Vector mode, S32 -> BF16)",
    0b100100: "QS322BF16_PRE (Scalar mode, S32 -> BF16)",
}

relu_modes = {
    0b000: "No ReLU",
    0b001: "Normal ReLU",
    0b010: "Scalar ReLU",
    0b011: "Vector ReLU",
    0b100: "PWL Mode",
}

clip_relu_modes = {
    0b0: "No Clip ReLU",
    0b1: "Clip ReLU (Scalar mode)",
}


class Ascend950FixpAnalyzer(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.instr_names = ("FIX_L0C_TO_DST",)

    @staticmethod
    def get_name() -> str:
        return "ascend950_fixp_analyzer"

    def get_fixp_instrs(self) -> List[sqlite3.Row]:
        instr_list = str(self.instr_names).replace(",)", ")")
        executed_instrs = database_handler.execute(
            """
        SELECT ExtendParams, ExecutedInstructions.ExecInstrId,
               ExecInstrName
        FROM ExecutedInstructions
        WHERE ExecInstrName IN {}
        ORDER BY ExecutedInstructions.ExecInstrId
        """.format(instr_list)
        )
        return executed_instrs.fetchall()

    def process_instruction(self, instr: sqlite3.Row) -> Optional[FixpParams]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        n = ep.n
        m = ep.m
        if n is None or m is None:
            return None

        quant_pre = ep.quant_pre or 0
        relu_pre = ep.relu_pre or 0
        clip_pre = ep.clip_pre or 0
        relu_post = ep.relu_post or 0
        clip_post = ep.clip_post or 0

        return FixpParams(
            n=n,
            m=m,
            l2_cache_ctrl_mode=None,
            hw_checks_unit_flag=False,
            hw_sets_unit_flag=False,
            pre_quant_mode=pre_quant_modes.get(quant_pre, f"Unknown({quant_pre})"),
            pre_relu_mode=relu_modes.get(relu_pre, f"Unknown({relu_pre})"),
            pre_clip_relu_mode=clip_relu_modes.get(clip_pre, f"Unknown({clip_pre})"),
            f32_channel_split_enable=False,
            post_clip_relu_mode=clip_relu_modes.get(clip_post, f"Unknown({clip_post})"),
            post_relu_mode=relu_modes.get(relu_post, f"Unknown({relu_post})"),
            nz2nd_enable=False,
            nz2dn_enable=False,
            c0_padding_enable=None,
            dst_mode=None,
            dst_ub_mask=0,
            processed_bytes=ep.processed_bytes or 0,
        )

    @staticmethod
    def add_fixp(instr_id: int, fixp_params: FixpParams) -> None:
        fixp_values = [instr_id, *vars(fixp_params).values()]
        database_handler.execute(
            f"""
            INSERT INTO FixpParams
            VALUES ({",".join("?" * len(fixp_values))})
            """,
            fixp_values,
        )

    def process(self) -> None:
        fixp_instrs = self.get_fixp_instrs()
        for instr in fixp_instrs:
            fixp_params = self.process_instruction(instr)
            if fixp_params is not None:
                self.add_fixp(instr["ExecInstrId"], fixp_params)
