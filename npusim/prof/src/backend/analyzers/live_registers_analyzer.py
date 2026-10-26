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

import collections
import dataclasses
from enum import IntEnum
import logging as log
import re
import sqlite3
from typing import Dict, List, OrderedDict, Tuple

from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


@dataclasses.dataclass
class RegisterState:
    gpr: List[int]


@dataclasses.dataclass
class LiveMapState:
    gpr: List[int]


class RegisterStateType(IntEnum):
    NOT_USED = 0
    USE = 1
    DEF = 2


class LiveRegistersAnalyzer(IAnalyzer):
    # Need to cover the whole ISA with def use mapping
    _def_use_mapping_gpr = {
        # key: instruction name
        # value: list of X/V register indices that define new register (def),
        # unlisted X/V register indices (if any)
        # are considered register uses (use).
        #
        # Important: instructions that write into a register, but depend
        # on previous data in it should be considered USE instructions!
        #
        # Indexing there considers only X and V-registers.
        # For example:
        # LDP_XI_XJ_XN instruction with such params obtained from sim logs:
        # log example: dtype:B16, XI:X2=0, XJ:X4=0, XN:X2=0x10303e, IMM:0,
        # log example: >          [  0  ]  [  1  ]  [     2      ]
        # Instruction result will be saved into XI and XJ (0, 1).
        "ADD_IMM": [0],
        "ADD": [0],
        "AND": [0],
        "BAR": [],
        "CLZ": [0],
        "CMP_IMM": [],
        "CMP": [],
        "CMPN": [0],
        "CONV": [0],
        "DC_PRELOAD_XN_IMM": [],
        "DC_PRELOAD_XN_XM": [],
        "DCCI": [],
        "DIV": [0],
        "END": [],
        "INSERT_XD_XN": [],
        "INSERT_XD": [],
        "JUMP": [],
        "JUMPC": [],
        "JUMPCMP": [],
        "LD_XD_XN_IMM": [0],
        "LD_XD_XN": [0],
        "LDP_XI_XJ_XN": [0, 1],
        "MADD": [],
        "MAX": [0],
        "MIN": [0],
        "MOV_OUT_TO_UB": [],
        "MOV_SPR_XN": [],
        "MOV_SRC_TO_DST_ALIGN": [],
        "MOV_UB_TO_OUT": [],
        "MOV_XD_IMM": [0],
        "MOV_XD_SPR": [0],
        "MOV_XD_XN": [0],
        "MOVEMASK": [],
        "MOVK": [0],
        "MOVX8_XD_IMM": [0],
        "MUL_IMM": [0],
        "MUL": [0],
        "NEG": [0],
        "NOP_PIPE": [],
        "NOP": [],
        "NOT": [0],
        "OR": [0],
        "PRELOAD": [],
        "REM": [0],
        "SBCNT": [0],
        "SBITSET": [],
        "SEL": [0],
        "SET_FLAG": [],
        "SFF": [0],
        "SHL": [],
        "SHR": [],
        "SIGNEXT": [0],
        "ST_DEV_XD_XN_IMM12": [],
        "ST_XD_XN_IMM": [],
        "ST_XD_XN": [],
        "STI_XN_IMM": [],
        "STI_XN_XM": [],
        "STP_XI_XJ_XN": [],
        "SUB_IMM": [0],
        "SUB": [0],
        "WAIT_FLAG_DEV": [],
        "WAIT_FLAG_DEVI": [],
        "WAIT_FLAG": [],
        "XOR": [0],
        "ZEROEXT": [0],
        "RV_CHISTv2": [0],
        "RV_DHISTv2": [0],
        "RV_VABS_FP": [0],
        "RV_VABS_INT": [0],
        "RV_VADD": [0],
        "RV_VADDC": [0],
        "RV_VADDCS": [0],
        "RV_VADDS": [0],
        "RV_VAND": [0],
        "RV_VAXPY": [0],
        "RV_VBR": [0],
        "RV_VCADD": [0],
        "RV_VCADDv2": [0],
        "RV_VCGADDv2": [0],
        "RV_VCGMAXv2": [0],
        "RV_VCI": [0],
        "RV_VCMAX": [0],
        "RV_VCMAXv2": [0],
        "RV_VCMIN": [0],
        "RV_VCMP_EQ": [],
        "RV_VCMP_GE": [],
        "RV_VCMP_GT": [],
        "RV_VCMP_LE": [],
        "RV_VCMP_LT": [],
        "RV_VCMP_NE": [],
        "RV_VCMPS_EQ": [],
        "RV_VCMPS_GE": [],
        "RV_VCMPS_GT": [],
        "RV_VCMPS_LE": [],
        "RV_VCMPS_LT": [],
        "RV_VCMPS_NE": [],
        "RV_VCVT_F2F": [0],
        "RV_VCVT_F2I": [0],
        "RV_VCVT_I2F": [0],
        "RV_VDINTLV": [0],
        "RV_VDINTLVv2": [0],
        "RV_VDIV": [0],
        "RV_VDUP": [0],
        "RV_VDUPS": [0],
        "RV_VEXP": [0],
        "RV_VEXPDIF": [0],
        "RV_VGATHER2": [0],
        "RV_VGATHERB": [0],
        "RV_VINTLV": [0],  # not sure this is calced correctly
        "RV_VINTLVv2": [0],
        "RV_VLD": [0],
        "RV_VLDI": [0],
        "RV_VLDS": [0],
        "RV_VLDU": [0],
        "RV_VLDUI": [0],
        "RV_VLDUS": [0],
        "RV_VLN": [0],
        "RV_VLRELU": [0],
        "RV_VMADD": [],
        "RV_VMAX": [0],
        "RV_VMAXS": [0],
        "RV_VMIN": [0],
        "RV_VMINS": [0],
        "RV_VMOV": [0],
        "RV_VMUL": [0],
        "RV_VMULA": [],
        "RV_VMULL": [0],
        "RV_VMULS": [0],
        "RV_VNEG_FP": [0],
        "RV_VNEG_INT": [0],
        "RV_VNOT": [0],
        "RV_VOR": [0],
        "RV_VSCATTER": [1],
        "RV_VSEL": [0],
        "RV_VSELR": [0],
        "RV_VSHLI": [0],
        "RV_VSHLS": [0],
        "RV_VSHR": [0],
        "RV_VSHRI": [0],
        "RV_VSHRS": [0],
        "RV_VSLDB": [0],
        "RV_VSQRT": [0],
        "RV_VSQZ": [0],
        "RV_VSSTB": [0],
        "RV_VST": [],
        "RV_VSTI": [],
        "RV_VSTS": [],
        "RV_VSTUI": [],
        "RV_VSTUR": [],
        "RV_VSTUS": [],
        "RV_VSUB": [0],
        "RV_VTRC": [0],
        "RV_VXOR": [0],
    }

    def __init__(self) -> None:
        super().__init__()
        self._live_regs: Dict[int, Tuple[int, int, int]] = dict()
        self._scalar_instrs: List[sqlite3.Row] = list()
        self._rvec_instrs: List[sqlite3.Row] = list()

    @staticmethod
    def get_name() -> str:
        return "live_registers"

    @staticmethod
    def _get_instrs_of_types(types: List[InstrType]) -> List[sqlite3.Row]:
        return database_handler.execute(
            """
        SELECT *
        FROM ExecutedInstructions
        WHERE InstrTypeId IN {}
        GROUP BY SourceInstrAddr
        ORDER BY SourceInstrAddr
        """.format(f"({','.join([str(type.value) for type in types])})")
        ).fetchall()

    @staticmethod
    def _get_registers_of_type(reg_type: str, params: str, max_reg_num: int) -> List[int]:
        if params is None:
            return []
        if reg_type == "X":
            expr = rf":{reg_type}([0-9a-f]+)="
        elif reg_type == "V":
            expr = rf"{reg_type}[dnm]\[([0-9a-f]+)\]"
        else:
            raise ValueError("Incorrect reg_type. Supported values: 'X', 'V'")
        r = re.findall(expr, params)
        regs = list(filter(lambda x: x < max_reg_num, [int(reg, base=10) for reg in r]))
        return regs

    def _process_instructions(self, instrs: List[sqlite3.Row], registers_prefix: str, num_of_registers: int) -> None:
        states: Dict[int, RegisterState] = dict()
        for instr in instrs:
            addr = instr["SourceInstrAddr"]
            name = instr["ExecInstrName"]
            params = instr["ExecInstrParams"]
            gprs = self._get_registers_of_type(registers_prefix, params, num_of_registers)
            s = RegisterState([RegisterStateType.NOT_USED] * num_of_registers)
            if gprs and name not in self._def_use_mapping_gpr.keys():
                log.debug(f"Failed to find def/use mapping for instruction '{name}', params {params}")
                raise RuntimeError(f"Unsupported instruction '{name}'")
            for index, gpr in enumerate(gprs):
                is_def = index in self._def_use_mapping_gpr[name]
                if not is_def:
                    s.gpr[gpr] = RegisterStateType.USE
                else:
                    s.gpr[gpr] = RegisterStateType.DEF
            states[addr] = s

        live_map = self._build_liveness_map(states, instrs, num_of_registers)
        for (exec_instr_id, _), map_state in live_map.items():
            self._add_live_registers(exec_instr_id, sum(map_state.gpr), 0)

    def _build_liveness_map(
        self,
        states: Dict[int, RegisterState],
        instrs: List[sqlite3.Row],
        max_reg_count: int,
    ) -> OrderedDict[Tuple[int, int], LiveMapState]:
        live_map: OrderedDict[Tuple[int, int], LiveMapState] = collections.OrderedDict()
        for instr in instrs:
            map_state = LiveMapState([RegisterStateType.NOT_USED] * max_reg_count)
            addr = instr["SourceInstrAddr"]
            if addr not in states:
                continue
            state = states[addr]
            for gpr_idx, gpr_state in enumerate(state.gpr):
                if gpr_state == RegisterStateType.USE:
                    for _, map in reversed(live_map.items()):
                        if map.gpr[gpr_idx] != 0:
                            break
                        map.gpr[gpr_idx] = RegisterStateType.USE
                if gpr_state in (RegisterStateType.USE, RegisterStateType.DEF):
                    map_state.gpr[gpr_idx] = RegisterStateType.USE
            live_map[
                instr["ExecInstrId"],
                instr["SourceInstrAddr"] + database_handler.get_start_pc(),
            ] = map_state
        return live_map

    def _process_instrs(self) -> None:
        self._scalar_instrs = self._get_instrs_of_types([InstrType.SCALAR])
        self._process_instructions(self._scalar_instrs, registers_prefix="X", num_of_registers=32)
        self._rvec_instrs = self._get_instrs_of_types(
            [
                InstrType.RVECEX,
                InstrType.RVECLP,
                InstrType.RVECSU,
                InstrType.RVECST,
                InstrType.RVECLD,
            ]
        )
        self._process_instructions(self._rvec_instrs, registers_prefix="V", num_of_registers=32)

    def process(self) -> None:
        self._process_instrs()
        self._store_live_registers()

    def _add_live_registers(self, exec_instr_id: int, gprs_count: int, shared_count: int) -> None:
        self._live_regs[exec_instr_id] = (exec_instr_id, gprs_count, shared_count)

    def _store_live_registers(self) -> None:
        database_handler.executemany(
            """
            INSERT INTO LiveRegisters
            (ExecInstrId, LiveGPRegisters, LiveSharedRegisters)
            VALUES (?, ?, ?)
        """,
            list(self._live_regs.values()),
        )

    def cleanup(self) -> None:
        self._live_regs.clear()
        self._scalar_instrs.clear()
        self._rvec_instrs.clear()
