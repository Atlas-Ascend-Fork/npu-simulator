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

import abc
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Optional, Any

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.biprof_constants import BYTES_IN_GB
from npusim.prof.src.common.extend_params import ExtendParams


@dataclass
class DMAParamsInfo(abc.ABC):
    pass


@dataclass
class DMAInstrInfo:
    params_info: Optional[DMAParamsInfo]
    processed_bytes: Optional[int]


class DMAMovAnalyzerBase(IAnalyzer):
    def __init__(self) -> None:
        self.instr_params: Dict[str, Dict[str, Tuple]] = {}
        self.used_addrs: Set[int] = set()

    @staticmethod
    def get_instr_exec_time(exec_ticks: int, aicore_freq_hz: float) -> Any:
        return (1 / aicore_freq_hz) * exec_ticks

    @staticmethod
    def convert_raw_to_gbs(raw_metric, exec_time) -> Any:
        return raw_metric / exec_time / BYTES_IN_GB

    def get_mte_instrs(self) -> List[sqlite3.Row]:
        instr_list = str(tuple(self.instr_params.keys())).replace(",)", ")")
        executed_instrs = database_handler.execute(
            """
        SELECT ExtendParams, ExecutedInstructions.ExecInstrId,
               ExecInstrName, ExecInstrTickStart, ExecInstrTickEnd
        FROM ExecutedInstructions
        WHERE ExecInstrName IN {}
        ORDER BY ExecutedInstructions.ExecInstrId
        """.format(instr_list)
        )
        result: List[sqlite3.Row] = executed_instrs.fetchall()
        return result

    @staticmethod
    def save_dma_params_info(instr_id: int, params_info) -> None:
        raise NotImplementedError

    @staticmethod
    def save_dma_processed_bytes(instr_id: int, processed_bytes: int, memory_traffic: float, cache_hit: bool) -> None:
        database_handler.execute(
            """
        INSERT INTO DmaMovProcessedBytes
        (ExecInstrId, ProcessedBytes, MemoryTraffic, CacheHit)
        VALUES
        (?, ?, ?, ?)
        """,
            (instr_id, processed_bytes, memory_traffic, cache_hit),
        )

    def process_instruction(self, instr: sqlite3.Row) -> Optional[DMAInstrInfo]:
        raise NotImplementedError

    def process(self) -> None:
        mte_instrs = self.get_mte_instrs()
        aicore_freq_hz = database_handler.get_frequency_ghz() * 10**9
        for instr in mte_instrs:
            dma_info = self.process_instruction(instr)
            if dma_info is None:
                continue
            if dma_info.params_info is not None:
                self.save_dma_params_info(instr["ExecInstrId"], dma_info.params_info)
            if dma_info.processed_bytes is not None:
                exec_time = self.get_instr_exec_time(
                    instr["ExecInstrTickEnd"] - instr["ExecInstrTickStart"],
                    aicore_freq_hz,
                )
                ep = ExtendParams.parse(instr["ExtendParams"])
                mem_addr = ep.src_addr if ep.src_addr is not None else ep.dst_addr
                if mem_addr is None:
                    continue
                self.save_dma_processed_bytes(
                    instr["ExecInstrId"],
                    dma_info.processed_bytes,
                    self.convert_raw_to_gbs(dma_info.processed_bytes, exec_time),
                    mem_addr in self.used_addrs,
                )
                self.used_addrs.add(mem_addr)
