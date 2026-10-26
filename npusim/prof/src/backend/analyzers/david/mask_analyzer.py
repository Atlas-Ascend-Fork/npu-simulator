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

from typing import List, Tuple, Optional, Iterator
from sqlite3 import Row

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams


ExecMaskItem = Tuple[int, int]


class DatabaseManager:
    @staticmethod
    def iter_instructions() -> Iterator[Row]:
        return database_handler.iter_rows("""
        SELECT ExecInstrId, ExtendParams
        FROM ExecutedInstructions
        """)

    @staticmethod
    def save_masks(batch: List[ExecMaskItem]) -> None:
        database_handler.executemany(
            """
        INSERT INTO ExecutionMasks (ExecInstrId, Mask)
        VALUES (?, ?)
        """,
            batch,
        )


class Ascend950MaskAnalyzer(IAnalyzer):
    # Computes the effective execution mask for each instruction.
    # The effective mask = predicate_mask & execution_mask (bitwise AND).
    # This determines which threads in a warp are actually active.
    # Skips instructions that don't have both prdct_mask and exec_mask params.
    BATCH_SIZE = 10_000

    @staticmethod
    def get_name() -> str:
        return "ascend950_mask_analyzer"

    @staticmethod
    def calc_mask(row: Row) -> Optional[ExecMaskItem]:
        ep = ExtendParams.parse(row["ExtendParams"])
        prdct_mask = ep.prdct_mask
        exec_mask = ep.exec_mask
        if prdct_mask is None or exec_mask is None:
            return None
        return (row["ExecInstrId"], prdct_mask & exec_mask)

    def process(self) -> None:
        batch: List[ExecMaskItem] = []
        for instr in DatabaseManager.iter_instructions():
            item = self.calc_mask(instr)
            if item is None:
                continue
            batch.append(item)
            if len(batch) >= self.BATCH_SIZE:
                DatabaseManager.save_masks(batch)
                batch.clear()
        if batch:
            DatabaseManager.save_masks(batch)
            batch.clear()
