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
from typing import List, Dict, Any, Tuple, Iterator

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams


class SPRInstrInfoAnalyzer(IAnalyzer):
    spr_instrs: Dict[str, List[str]]
    mov_align_sprs: Dict[Tuple[str, str], List[str]]

    def __init__(self):
        super().__init__()
        # instrs for which I don't have logs to verify:
        # GATHER_OUT_TO_UB
        # SCATTER_UB_TO_OUT
        # MOVP
        # MOV_OUT_TO_L1_V2 (no ALIGN)

        self.spr_instrs = {
            "ND_DMA": [
                "PAD_CNT_NDDMA",
                "PAD_VAL_NDDMA",
                "LOOP0_STRIDE_NDDMA",
                "LOOP1_STRIDE_NDDMA",
                "LOOP2_STRIDE_NDDMA",
                "LOOP3_STRIDE_NDDMA",
                "LOOP4_STRIDE_NDDMA",
            ],
            "FIX_L0C_TO_DST": [
                "FPC",
                "QUANT_PRE",
                "RELU_ALPHA",
                "QUANT_POST",
                "FIX_CLIP_RELU",
                "LOOP3_PARA",
                "LOOP4_PARA",
                "CHANNEL_PARA",
                "ELT_ANTIQ_PARA",
                "ELT_SRC_PARA",
                "CTRL",
            ],
            "CONV": ["CTRL"],
            "SEL": ["CONDITION_FLAG"],
            "LD_XD_XN_IMM": ["CTRL"],
            "LD_XD_XN": ["CTRL"],
            "LDP_XI_XJ_XN": ["CTRL"],
            "ST_XD_XN_IMM": ["CTRL"],
            "ST_XD_XN": ["CTRL"],
            "STI_XN_IMM": ["CTRL"],
            "STI_XN_XM": ["CTRL"],
            "STP_XI_XJ_XN": ["CTRL"],
            "JUMPC": ["CONDITION_FLAG"],
            "JUMPCI": ["CONDITION_FLAG"],
            "CALL": ["CTRL"],
            "CALLI": ["CTRL"],
            "CALLR": ["CTRL"],
            "MOVEMASK": ["MASK"],
            "VMS4v2": ["VMS4_SR"],
            "RV_VSTUR": ["SQZN", "AR"],
            "RV_VSTAR": ["AR"],
            "RV_SPRSTI": ["AR"],
            "RV_SPRSTS": ["AR"],
            "RV_VSQZ": ["SQZN"],
            "RV_VCVTFI": ["CTRL"],
            "RV_VCVTFF": ["CTRL"],
            "RV_VCVTFF2": ["CTRL"],
            "RV_VCVTII": ["CTRL"],
            "RV_VTRC": ["CTRL"],
            "MOV_OUT_TO_L1_MULTI_ND2NZ": ["MTE2_NZ_PARA"],
            "MMAD": ["CTRL"],
            "MMAD_MX": ["CTRL"],
        }
        self.src_dst_spr_instrs = {
            "MOV_SRC_TO_DST_ALIGNv2": {
                ("UB", "OUT"): [
                    "LOOP_SIZE_UBTOOUT",
                    "LOOP1_STRIDE_UBTOOUT",
                    "LOOP2_STRIDE_UBTOOUT",
                    "CTRL",
                    "PCIE_WR_CTRL",
                ],
                ("OUT", "UB"): [
                    "PAD_VAL_OUTTOUB",
                    "LOOP_SIZE_OUTTOUB",
                    "LOOP1_STRIDE_OUTTOUB",
                    "LOOP2_STRIDE_OUTTOUB",
                    "PCIE_RD_CTRL",
                ],
                ("OUT", "L1"): [
                    "PAD_VAL_OUTTOL1",
                    "LOOP_SIZE_OUTTOL1",
                    "LOOP1_STRIDE_OUTTOL1",
                    "LOOP2_STRIDE_OUTTOL1",
                ],
            },
            "LOAD_2Dv2": {("OUT", "L1"): ["MTE2_QTABLE0", "MTE2_QTABLE1", "MTE2_SRC_PARA"]},
            "SET_2D": {("UNDEF", "L1"): ["L0_SET_VALUE"]},
            "LOAD_3Dv2": {
                ("L1", "L0A"): [
                    "FMATRIX",
                    "FMATRIX_B",
                    "FMATRIX_DUAL_0",
                    "FMATRIX_DUAL_1",
                    "L3D_RPT",
                    "L3D_RPT_B",
                    "PADDING",
                    "PADDING_B",
                ],
                ("L1", "L0B"): [
                    "FMATRIX",
                    "FMATRIX_B",
                    "L3D_RPT",
                    "L3D_RPT_B",
                    "PADDING",
                    "PADDING_B",
                ],
            },
        }

    @staticmethod
    def get_name() -> str:
        return "ascend950_spr_instr_info_analyzer"

    def iter_instr_info(self, core_id: int, core_type_id: int) -> Iterator[sqlite3.Row]:
        instr_list = str(tuple(self.spr_instrs.keys()) + tuple(self.src_dst_spr_instrs.keys()))
        instr_list = instr_list.replace(",)", ")")
        return database_handler.iter_rows(
            """
        SELECT ExecInstrId AS ID, ExecInstrName AS Name,
               ExecInstrTickStart AS Tick, ExtendParams AS Params
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId=:core_id AND
              CoreTypeId=:core_type_id AND
              ExecInstrName IN {}
        ORDER BY Tick
        """.format(instr_list),
            {"core_id": core_id, "core_type_id": core_type_id},
        )

    def get_spr_info(self, core_id: int, core_type_id: int) -> List[Any]:
        return database_handler.execute(
            """
        SELECT EventTick as Tick, SprName, SprValue
        FROM SprWriteEvents
        WHERE CoreId=:core_id AND
              CoreTypeId=:core_type_id
        ORDER BY Tick
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchall()

    def match_spr_to_instr(self, core_id: int, core_type_id: int) -> List[Tuple[int, str, str]]:
        current_spr: Dict[str, str] = {}
        spr_info = self.get_spr_info(core_id, core_type_id)
        res: List[Tuple[int, str, str]] = []

        spr_index = 0
        for instr in self.iter_instr_info(core_id, core_type_id):
            while spr_index < len(spr_info) and spr_info[spr_index]["Tick"] < instr["Tick"]:
                spr_event = spr_info[spr_index]
                current_spr[spr_event["SprName"]] = spr_event["SprValue"]
                spr_index += 1
            if instr["Name"] in self.src_dst_spr_instrs.keys():
                ep = ExtendParams.parse(instr["Params"])
                key = (ep.src_mem, ep.dst_mem)
                if key not in self.src_dst_spr_instrs[instr["Name"]].keys():
                    continue
                for spr_name in self.src_dst_spr_instrs[instr["Name"]][key]:
                    if spr_name in current_spr:
                        res.append((instr["ID"], spr_name, current_spr[spr_name]))
                continue
            for spr_name in self.spr_instrs[instr["Name"]]:
                if spr_name in current_spr:
                    res.append((instr["ID"], spr_name, current_spr[spr_name]))
        return res

    def save_spr_info(self, spr_info: List[Tuple[int, str, str]]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SprInfoPerInstr(ExecInstrId, SprName, SprValue)
        VALUES (?, ?, ?)
        """,
            spr_info,
        )

    def process(self):
        spr_info = []
        for core_id in self.get_active_cores():
            for core_type_id in self.get_active_core_types():
                spr_info += self.match_spr_to_instr(core_id, core_type_id)
        self.save_spr_info(spr_info)
