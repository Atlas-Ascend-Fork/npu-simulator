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

from typing import List, Tuple, Iterator
from sqlite3 import Row

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams


SIMTExecMasksItem = Tuple[int, int, int, int, float]


class DatabaseManager:
    @staticmethod
    def iter_instructions() -> Iterator[Row]:
        return database_handler.iter_rows("""
        SELECT ExecInstrId, ExtendParams
        FROM ExecutedInstructions
        WHERE ExecInstrName LIKE '%SIMT%'
        """)

    @staticmethod
    def save_masks(data: List[SIMTExecMasksItem]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SIMTExecutionMasks(
         ExecInstrId, PredicateMask,
         ExecutionMask, FullMask,
         BranchEfficiency)
        VALUES (?, ?, ?, ?, ?)
        """,
            data,
        )


class Ascend950BranchEfficiencyAnalyzer(IAnalyzer):
    # Branch efficiency = percentage of threads in a warp that take the
    # taken path. Computed from execution masks in SIMT instructions:
    #   full_mask = exec_mask & prdct_mask  (threads that executed)
    #   efficiency = count('1' bits in full_mask) / 32 * 100
    # A value of 100% means all threads agree (no divergence).
    #
    # With the contract DB, prdct_mask/exec_mask/branch_efficiency are
    # provided directly in ExtendParams.  When branch_efficiency is present
    # we use it as-is; otherwise we derive it from the masks.
    BATCH_SIZE = 10_000

    @staticmethod
    def get_name() -> str:
        return "ascend950_branch_efficiency_analyzer"

    @classmethod
    def iter_masks(cls, iter_instructions: Iterator[Row]) -> Iterator[SIMTExecMasksItem]:
        for instr in iter_instructions:
            ep = ExtendParams.parse(instr["ExtendParams"])
            exec_mask = ep.exec_mask
            prdct_mask = ep.prdct_mask
            if exec_mask is None or prdct_mask is None:
                continue

            instr_id = int(instr["ExecInstrId"])
            full_mask = exec_mask & prdct_mask
            branch_efficiency = ep.branch_efficiency
            if branch_efficiency is None:
                branch_efficiency = 100 * (bin(full_mask).count("1") / 32)

            yield (
                instr_id,
                prdct_mask,
                exec_mask,
                full_mask,
                float(branch_efficiency),
            )

    def process(self) -> None:
        batch: List[SIMTExecMasksItem] = []
        iter_instructions = DatabaseManager.iter_instructions()
        for row in self.iter_masks(iter_instructions):
            batch.append(row)
            if len(batch) >= self.BATCH_SIZE:
                DatabaseManager.save_masks(batch)
                batch.clear()
        if batch:
            DatabaseManager.save_masks(batch)
            batch.clear()
