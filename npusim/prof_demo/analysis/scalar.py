# -*- coding: utf-8 -*-
#!/usr/bin/env python3
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
from itertools import groupby
import sqlite3
from statistics import mean
from typing import List

from npusim.prof_demo.config import config
from npusim.prof_demo.enums import InstrType
from npusim.prof_demo.database import database_handler, BackendDBManager
from npusim.prof_demo.utils import edge_instructions


@dataclass
class SingleIPCItem:
    exec_instr_id: int
    window_start_tick: int
    value: float


class Ascend950ScalarInstructionPerCycleDynamicAnalyzer:
    @staticmethod
    def get_scalar_instructions(core_id: int, core_type_id: int) -> List[sqlite3.Row]:
        return database_handler.execute(
            """
            SELECT
                ExecInstrId, ExecInstrTickStart, ExecInstrTickEnd
            FROM ExecutedInstructions
            WHERE
                ExecutedInstructions.ExecInstrCoreId = :core_id
                AND ExecutedInstructions.CoreTypeId = :core_type_id
                AND InstrTypeId = :instr_type_id
            ORDER BY ExecInstrTickStart
        """,
            {
                "core_id": core_id,
                "core_type_id": core_type_id,
                "instr_type_id": InstrType.SCALAR.value,
            },
        ).fetchall()

    @staticmethod
    def calc_values_dynamic(instructions: List[sqlite3.Row], sliding_window_size: int) -> List[SingleIPCItem]:
        result: List[SingleIPCItem] = []

        i = 0
        while i <= len(instructions) - sliding_window_size:
            window_start_tick = instructions[i]["ExecInstrTickStart"]
            window_end_tick = instructions[i + sliding_window_size - 1]["ExecInstrTickEnd"]
            mid_exec_instr_id = instructions[i + int(sliding_window_size / 2)]["ExecInstrId"]

            tick = window_start_tick
            value = sliding_window_size / max(1, window_end_tick - window_start_tick)

            result.append(SingleIPCItem(mid_exec_instr_id, tick, value))
            i += 1
        return result

    @staticmethod
    def take_tick_averages(items: List[SingleIPCItem]) -> List[SingleIPCItem]:
        result = []
        for tick, group in groupby(items, key=lambda x: x.window_start_tick):
            group_items = list(group)
            exec_instr_ids = [item.exec_instr_id for item in group_items]
            mean_ipc = mean([item.value for item in group_items])
            for exec_instr_id in exec_instr_ids:
                result.append(SingleIPCItem(exec_instr_id, tick, mean_ipc))
        return result

    @staticmethod
    def save_ipc_dynamic(core_id: int, core_type_id: int, data: List[SingleIPCItem]) -> None:
        database_handler.executemany(
            """
        INSERT INTO ScalarIpcDynamic
            (CoreId, CoreTypeId, ExecInstrId, Tick, IPC)
        VALUES
            (?, ?, ?, ?, ?)
        """,
            [(core_id, core_type_id, item.exec_instr_id, item.window_start_tick, item.value) for item in data],
        )

    def process(self) -> None:
        sliding_window_size = config.sliding_window

        for core_id in BackendDBManager.get_active_cores():
            for core_type_id in BackendDBManager.get_active_core_types():
                scalar_instructions = self.get_scalar_instructions(core_id, core_type_id)
                if not scalar_instructions:
                    continue

                # If the number of instructions is smaller,
                # the window shrinks to fit
                sliding_window_size_actual = (
                    sliding_window_size if len(scalar_instructions) >= sliding_window_size else len(scalar_instructions)
                )

                # Calculate the average value for each tick
                # and store it for every instruction
                data = self.calc_values_dynamic(scalar_instructions, sliding_window_size_actual)
                data = self.take_tick_averages(data)

                # Process edge instructions
                start_instrs, end_instrs = edge_instructions(scalar_instructions, sliding_window_size_actual)
                first, last = data[0], data[-1]
                start_items = [
                    SingleIPCItem(i["ExecInstrId"], first.window_start_tick, first.value) for i in start_instrs
                ]
                end_items = [SingleIPCItem(i["ExecInstrId"], last.window_start_tick, last.value) for i in end_instrs]
                data = start_items + data + end_items

                # Save data
                self.save_ipc_dynamic(core_id, core_type_id, data)
