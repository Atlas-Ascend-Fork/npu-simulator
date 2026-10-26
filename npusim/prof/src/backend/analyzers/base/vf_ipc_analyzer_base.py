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

from dataclasses import dataclass
import sqlite3
from typing import Iterable, List, Dict, Set, Iterator

from npusim.prof.src.common.biprof_enum import InstrType
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


@dataclass(frozen=True)
class VfRawData:
    vf_instr_id: int
    instrs_of_vf: List[sqlite3.Row]


@dataclass(frozen=True)
class VfIPCItemBase:
    exec_instr_id: int
    instr_type: InstrType
    window_start_tick: int


class Ascend950VfIPCAnalyzerBase(IAnalyzer):
    @staticmethod
    def get_supported_vfs() -> List[str]:
        raise NotImplementedError

    @staticmethod
    def get_supported_groups() -> Dict[str, Set[InstrType]]:
        raise NotImplementedError

    @classmethod
    def get_supported_instr_types(cls) -> List[InstrType]:
        return [instr for group in cls.get_supported_groups().values() for instr in group]

    @staticmethod
    def get_excluded_instrs() -> List[str]:
        return []

    @staticmethod
    def make_index() -> None:
        database_handler.execute("""
            CREATE INDEX IF NOT EXISTS ExecutedInstructions_Index
            ON ExecutedInstructions(
                ExecInstrCoreId, CoreTypeId,
                ExecInstrTickStart, ExecInstrTickEnd)
        """)

    @staticmethod
    def drop_index() -> None:
        database_handler.execute("""
            DROP INDEX ExecutedInstructions_Index
        """)

    # Vector instructions processing is ordered
    @classmethod
    def get_vf_instrs(cls) -> List[sqlite3.Row]:
        supported_instrs_str = str(tuple(cls.get_supported_vfs())).replace(",)", ")")
        return database_handler.execute(
            """
            SELECT * FROM ExecutedInstructions
            WHERE ExecutedInstructions.ExecInstrName IN {0}
            ORDER BY ExecInstrCoreId, CoreTypeId, ExecInstrTickStart
        """.format(supported_instrs_str)
        ).fetchall()

    @classmethod
    def get_instrs_of_vf(cls, core_id: int, core_type_id: int, tick_start: int, tick_end: int) -> List[sqlite3.Row]:
        appendix_1 = ""
        excluded_instrs = cls.get_excluded_instrs()
        if excluded_instrs:
            appendix_1 = "AND EI.ExecInstrName NOT IN ({0})".format(",".join([f"'{type}'" for type in excluded_instrs]))

        return database_handler.execute(
            """
            SELECT * FROM ExecutedInstructions AS EI
            WHERE
                EI.ExecInstrCoreId = :core_id
                AND EI.CoreTypeId = :core_type_id
                AND EI.InstrTypeId IN ({0})
                AND EI.ExecInstrTickStart >= :tick_start
                AND EI.ExecInstrTickEnd <= :tick_end
                {1}
            ORDER BY ExecInstrTickStart, ExecInstrTickEnd
        """.format(
                ",".join([str(type.value) for type in cls.get_supported_instr_types()]),
                appendix_1,
            ),
            {
                "core_id": core_id,
                "core_type_id": core_type_id,
                "tick_start": tick_start,
                "tick_end": tick_end,
            },
        ).fetchall()

    def iter_vf_raw_data(self) -> Iterator[VfRawData]:
        for vf_instr in self.get_vf_instrs():
            core_id = int(vf_instr["ExecInstrCoreId"])
            core_type_id = int(vf_instr["CoreTypeId"])

            vf_instr_id = int(vf_instr["ExecInstrId"])
            vf_tick_start = int(vf_instr["ExecInstrTickStart"])
            vf_tick_end = int(vf_instr["ExecInstrTickEnd"])

            instrs_of_vf = self.get_instrs_of_vf(
                core_id,
                core_type_id,
                vf_tick_start,
                vf_tick_end,
            )

            yield VfRawData(vf_instr_id, instrs_of_vf)

    @classmethod
    def count_instrs_by_group(cls, instrs_of_vf: Iterable[sqlite3.Row]) -> List[int]:
        supported_groups = cls.get_supported_groups()
        type_to_group = {instr_type: i for i, group in enumerate(supported_groups.values()) for instr_type in group}

        counts = [0] * len(supported_groups)
        for instr in instrs_of_vf:
            idx = type_to_group.get(InstrType(int(instr["InstrTypeId"])))
            if idx is not None:
                counts[idx] += 1
        return counts

    @staticmethod
    def calc_vf_ipc_static(
        instrs_of_vf: List[sqlite3.Row],
        dispatched_instrs_count: int,
    ) -> float:
        if not instrs_of_vf:
            return 0.0
        difference = int(instrs_of_vf[-1]["ExecInstrTickEnd"]) - int(instrs_of_vf[0]["ExecInstrTickStart"])
        cycles = max(difference, 1)
        return dispatched_instrs_count / cycles

    def process_vfs(self, sliding_window_size: int, vf_raw_data: Iterator[VfRawData]) -> None:
        raise NotImplementedError

    def process(self) -> None:
        self.make_index()

        # Process
        vf_raw_data_iter = self.iter_vf_raw_data()
        self.process_vfs(
            biprof_ctx.sliding_window_vec,
            vf_raw_data_iter,
        )

        self.drop_index()
