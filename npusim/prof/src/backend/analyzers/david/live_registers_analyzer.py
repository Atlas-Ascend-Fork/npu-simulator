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

import bisect
import logging as log
import re
import sqlite3
from typing import Dict, List, Tuple

from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.live_registers_analyzer import (
    LiveRegistersAnalyzer,
    RegisterState,
    RegisterStateType,
)
from npusim.prof.src.common.exceptions import RequirementsIncomplete


class Ascend950LiveRegistersAnalyzer(LiveRegistersAnalyzer):
    # Live register analysis for SIMT instructions inside VF_SIMT blocks.
    # Uses def/use chains: for each SIMT instruction, determines which
    # registers it defines (writes) vs uses (reads). Then performs a
    # backward liveness analysis to count simultaneously-live registers.
    # This information is critical for assessing register pressure and
    # whether the kernel fits within the hardware's register file limits.
    #
    # _def_use_mapping_gpr maps instruction names to the list of register
    # operand indices that are definitions (all others are uses).
    SIMT_GPR_MAX_COUNT = 128
    SIMT_SPR_MAX_COUNT = 32

    # Need to cover the whole ISA with def use mapping
    _def_use_mapping_gpr = {
        **LiveRegistersAnalyzer._def_use_mapping_gpr,
        # key: instruction name
        # value: list of R register indices that define new register (def),
        # unlisted R register indices (if any)
        # are considered register uses (use).
        "SIMT_ATOM": [4],
        "SIMT_F2F": [1],
        "SIMT_F2I": [1],
        "SIMT_FADD_I": [1],
        "SIMT_FADD": [2],
        "SIMT_FDIV_I": [1],
        "SIMT_FDIV": [2],
        "SIMT_FEXPDIF": [2],
        "SIMT_FFMA_I": [2],
        "SIMT_FFMA": [3],
        "SIMT_FMNMX_I": [1],
        "SIMT_FMNMX": [2],
        "SIMT_FMUL_I": [1],
        "SIMT_FMUL": [2],
        "SIMT_FSETP_I": [],
        "SIMT_FSETP": [],
        "SIMT_I2F": [1],
        "SIMT_IADD_I": [1],
        "SIMT_IADD_X_I": [1],
        "SIMT_IADD_X": [2],
        "SIMT_IADD": [2],
        "SIMT_IMAD_I": [2],
        "SIMT_IMAD": [2, 3],
        "SIMT_IMNMX_I": [1],
        "SIMT_IMNMX": [2],
        "SIMT_IMUL_I": [1],
        "SIMT_IMUL": [2],
        "SIMT_ISETP_I": [],
        "SIMT_ISETP": [],
        "SIMT_LDG": [2],
        "SIMT_LDK": [1],
        "SIMT_LDS": [1],
        "SIMT_LEA_HI_X": [2],
        "SIMT_LEA": [2],
        "SIMT_LOP3_I": [2],
        "SIMT_LOP3": [3],
        "SIMT_MOV": [0],
        "SIMT_MOVI": [0],
        "SIMT_MUFU": [1],
        "SIMT_P2R": [1],
        "SIMT_PRMT": [3],
        "SIMT_RED": [],
        "SIMT_REDUX": [1],
        "SIMT_S2R": [0],
        "SIMT_SEL": [2],
        "SIMT_SHF": [2],
        "SIMT_SHFI": [2],
        "SIMT_SHFL": [2],
        "SIMT_STG": [],
        "SIMT_STK": [],
        "SIMT_STS": [],
    }

    def __init__(self) -> None:
        super().__init__()
        self._kernel_shared_regs_count: Dict[int, int] = dict()
        self._vf_simt: List[Tuple[int, int]] = list()
        self._simt_instrs: List[sqlite3.Row] = list()

    @staticmethod
    def get_name() -> str:
        return "ascend950_live_registers"

    def check_requirements(self) -> None:
        if not database_handler.get_analysis_status("vf_simt"):
            raise RequirementsIncomplete("VF SIMT analysis is required")

    @staticmethod
    def get_simt_instrs() -> List[sqlite3.Row]:
        simt_instr_type_list = (
            InstrType.RVECEX,
            InstrType.RVECLP,
            InstrType.RVECSU,
            InstrType.RVECST,
            InstrType.RVECLD,
        )
        simt_instr_types = [int(instr.value) for instr in simt_instr_type_list]
        return database_handler.execute(
            f"""
        SELECT *
        FROM ExecutedInstructions
        WHERE InstrTypeId IN ({",".join("?" * len(simt_instr_types))})
              AND ExecInstrName LIKE "SIMT_%"
        GROUP BY SourceInstrAddr
        ORDER BY SourceInstrAddr
        """,
            (*simt_instr_types,),
        ).fetchall()

    @staticmethod
    def get_vf_simt_instrs() -> List[sqlite3.Row]:
        return database_handler.execute("""
        SELECT *
        FROM ExecutedInstructions
        WHERE ExecInstrName="VF_SIMT"
        """).fetchall()

    @staticmethod
    def _get_simt_registers_of_type(reg_type: str, params: str, max_reg_num: int) -> List[int]:
        if params is None:
            return []
        r = re.findall(rf":([0-9a-f]+)\|{reg_type}\]", params)
        regs = list(filter(lambda x: x < max_reg_num, [int(reg, base=16) for reg in r]))
        return regs

    def _process_vf_simt(self) -> None:
        vf_simt_instrs = self.get_vf_simt_instrs()

        # Retrieve information about SIMT invocations and map SIMT kernel
        # instructions to specific VF_SIMT to get information about available
        # shared registers amount
        msg = """
        SELECT KernelAddr, VfSimtAddr
        FROM VfSimtInvocations
        """
        self._vf_simt = sorted([tuple(row) for row in database_handler.execute(msg)])

        for instr in vf_simt_instrs:
            gpr_count = 0
            shared_count = 0
            for simt_instr in self._simt_instrs:
                params = simt_instr["ExecInstrParams"]
                gpr_count = max(
                    [gpr_count]
                    + list(
                        filter(
                            lambda x: x < 127,
                            self._get_simt_registers_of_type("R", params, self.SIMT_GPR_MAX_COUNT),
                        )
                    )
                )
                shared_count = max(
                    [shared_count] + self._get_simt_registers_of_type("S", params, self.SIMT_SPR_MAX_COUNT)
                )
            self._add_live_registers(instr["ExecInstrId"], gpr_count, shared_count)
            self._kernel_shared_regs_count[instr["SourceInstrAddr"]] = shared_count

    def _process_simt_instructions(self) -> None:
        states: Dict[int, RegisterState] = {
            -1: RegisterState([RegisterStateType.NOT_USED] * self.SIMT_GPR_MAX_COUNT),
        }
        for instr in self._simt_instrs:
            addr = instr["SourceInstrAddr"]
            name = instr["ExecInstrName"]
            params = instr["ExecInstrParams"]
            gprs = self._get_simt_registers_of_type("R", params, self.SIMT_GPR_MAX_COUNT)
            s = RegisterState([RegisterStateType.NOT_USED] * self.SIMT_GPR_MAX_COUNT)
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
        del states[-1]

        live_map = self._build_liveness_map(states, self._simt_instrs, self.SIMT_GPR_MAX_COUNT)

        if not self._vf_simt:
            return

        for (addr, source_addr), map_state in live_map.items():
            idx = bisect.bisect_left(self._vf_simt, (source_addr, 0xFFFFFFF), 0, len(self._vf_simt)) - 1
            assert 0 <= idx < len(self._vf_simt)
            self._add_live_registers(
                addr,
                sum(map_state.gpr),
                self._kernel_shared_regs_count[self._vf_simt[idx][1]],
            )

    def _process_instrs(self) -> None:
        super()._process_instrs()
        self._simt_instrs = self.get_simt_instrs()
        self._process_vf_simt()
        self._process_simt_instructions()

    def cleanup(self) -> None:
        super().cleanup()
        self._kernel_shared_regs_count.clear()
        self._vf_simt.clear()
        self._simt_instrs.clear()
