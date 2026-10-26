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
from typing import List, Dict, Optional
from dataclasses import dataclass
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.pipe_dependency_analyzer import (
    PipeDependencyAnalyzer,
    PipeDependencyCategory,
)
from npusim.prof.src.common.biprof_enum import CoreType
from npusim.prof.src.common.extend_params import ExtendParams

VECCORE0 = "Vector 0"
VECCORE1 = "Vector 1"
CUBECORE = "Cube"

TOTAL_VECCORE0_SYNC_IDS = 16
TOTAL_VECCORE1_SYNC_IDS = 16
TOTAL_CUBECORE_SYNC_IDS = 32


class Ascend950PipeDependencyBlocksAnalyzer(PipeDependencyAnalyzer):
    # Intra-block SET/WAIT dependency tracker for cross-core synchronization.
    # SET_INTRA_BLOCK and WAIT_INTRA_BLOCK instructions use sync IDs to
    # coordinate between AIV0, AIV1, and AIC cores. This analyzer pairs
    # each SET with its corresponding WAIT to create flow dependency edges
    # in the pipe dependency graph.
    #
    # Sync ID counter layout (128 slots):
    #   SETTER counters [0..63]:  AIV0(0-15) | AIV1(0-15) | AIC(0-31)
    #   WAITER counters [64..127]: AIV0(0-15) | AIV1(0-15) | AIC(0-31)
    @dataclass
    class IntraBlockParams:
        exec_pipe: str
        sync_id: int

    def __init__(self) -> None:
        super().__init__()
        self.core_id_to_name_map = {
            CoreType.AIV0.value: VECCORE0,
            CoreType.AIV1.value: VECCORE1,
            CoreType.AIC.value: CUBECORE,
        }
        self.core_name_to_id_map = {
            VECCORE0: CoreType.AIV0.value,
            VECCORE1: CoreType.AIV1.value,
            CUBECORE: CoreType.AIC.value,
        }
        """
        We use auto-incremented counters to track access to each sync ID
        on each core: VECCORE0, VECCORE1, and CUBECORE have 16, 16, and 32
        counter slots respectively.
        Setter (a core calling SET instructions) and waiter (a core calling
        WAIT instructions) operate on independent counter slots, hence we
        need (16 + 16 + 32) x 2 = 128 counter slots implemented as list
        uint32 array:
        |       SETTER COUNTER SLOTS (0-63)      |
        | AIV0 (0-15) | AIV1 (0-15) | AIC (0-31) |
        | ---------------------------------------|
        |      WAITER COUNTER SLOTS (64-127)     |
        | AIV0 (0-15) | AIV1 (0-15) | AIC (0-31) |
        """
        self.sync_id_counters = [0] * (
            (TOTAL_VECCORE0_SYNC_IDS + TOTAL_VECCORE1_SYNC_IDS + TOTAL_CUBECORE_SYNC_IDS) * 2
        )

    @staticmethod
    def is_setter(instr_name: str) -> bool:
        return instr_name[0] == "S"

    @staticmethod
    def get_peer(core_type_id: int, sync_id: int) -> int:
        if core_type_id in (CoreType.AIV0.value, CoreType.AIV1.value):
            return CoreType.AIC.value
        if sync_id < TOTAL_VECCORE0_SYNC_IDS:
            return CoreType.AIV0.value
        return CoreType.AIV1.value

    def get_local_peer(self, core_type_id: int, sync_id: int) -> tuple:
        local = self.core_id_to_name_map[core_type_id]
        peer_id = self.get_peer(core_type_id, sync_id)
        peer = self.core_id_to_name_map[peer_id]
        return local, peer

    def process(self) -> None:
        syncs: Dict[int, int] = defaultdict(int)
        for instr in self.get_sync_instrs():
            sync_params = self.get_sync_params(instr)
            if sync_params is None:
                continue

            if instr["CoreTypeId"] == CoreType.AIV1.value:
                sync_params.sync_id += TOTAL_VECCORE0_SYNC_IDS

            is_setter = self.is_setter(instr["ExecInstrName"])
            if is_setter:
                setter, waiter = self.get_local_peer(instr["CoreTypeId"], sync_params.sync_id)
            else:
                waiter, setter = self.get_local_peer(instr["CoreTypeId"], sync_params.sync_id)
            index = self.get_sync_index(
                not is_setter,
                self.core_name_to_id_map[setter],
                self.core_name_to_id_map[waiter],
                sync_params.sync_id,
                instr["ExecInstrCoreId"],
            )
            instr_id = int(instr["ExecInstrId"])
            if syncs[index] == 0:
                # flow begin
                syncs[index] = instr_id
                flow_event_id = self.get_flow_event_id(index, syncs[index])
            else:
                # flow end
                flow_event_id = self.get_flow_event_id(index, syncs[index])
                syncs[index] = 0

            flow_event_name = self.get_flow_event_name(waiter, setter, sync_params.sync_id)
            category = PipeDependencyCategory.INTRA_BLOCKS.value
            self.store_pipe_dependency(instr_id, flow_event_id, flow_event_name, syncs[index] == 0, category)

    @staticmethod
    def get_sync_instrs() -> List[sqlite3.Row]:
        executed_instrs = database_handler.execute("""
            SELECT ExtendParams, ExecInstrId, ExecInstrName, ExecInstrCoreId, CoreTypeId
            FROM ExecutedInstructions
            WHERE ExecInstrName LIKE "WAIT_INTRA_BLOCK%" OR
                  ExecInstrName LIKE "SET_INTRA_BLOCK%"
            ORDER BY ExecInstrTickStart
            """)
        result: List[sqlite3.Row] = executed_instrs.fetchall()
        return result

    @staticmethod
    def get_sync_params(
        instr: sqlite3.Row,
    ) -> Optional["Ascend950PipeDependencyBlocksAnalyzer.IntraBlockParams"]:
        ep = ExtendParams.parse(instr["ExtendParams"])
        sync_id = ep.sync_id
        if sync_id is None:
            return None
        return Ascend950PipeDependencyBlocksAnalyzer.IntraBlockParams(ep.target_pipe or "", sync_id)

    def get_counter_offset(self, is_waiter: bool, waiter: int, sync_id: int) -> int:
        offset = sync_id
        if is_waiter is True:
            offset += TOTAL_VECCORE0_SYNC_IDS + TOTAL_VECCORE1_SYNC_IDS + TOTAL_CUBECORE_SYNC_IDS
        if waiter == CoreType.AIC.value:
            offset += TOTAL_VECCORE0_SYNC_IDS + TOTAL_VECCORE1_SYNC_IDS
        return offset

    def get_counter(self, is_waiter: bool, waiter: int, sync_id: int) -> int:
        offset = self.get_counter_offset(is_waiter, waiter, sync_id)
        counter: int = self.sync_id_counters[offset]
        self.sync_id_counters[offset] += 1
        return counter

    def get_sync_index(self, is_waiter: bool, setter: int, waiter: int, sync_id: int, core_id: int) -> int:
        """
        Here we generate unique key for quick identication of complementary
        pairs of SET/WAIT instructions:
        | Bits 23-16      | Bits 14-13 | Bits 12-11 | Bits 10-6 | Bits 5-0 |
        | Sync ID counter | Setter ID  | Waiter ID  | Sync ID   | Core ID  |
        """
        counter = self.get_counter(is_waiter, waiter, sync_id)
        index = counter << 16
        index |= setter << 13
        index |= waiter << 11
        index |= sync_id << 6
        index |= core_id
        return index

    @staticmethod
    def get_name() -> str:
        return "ascend950_pipe_dependency_blocks"

    @staticmethod
    def get_flow_event_name(waiter: str, setter: str, sync_id: int) -> str:
        return f"{waiter} awaiting {setter}, sync ID #{sync_id}"
