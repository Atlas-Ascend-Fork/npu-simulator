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
from typing import List
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.database_manager import BackendDBManager
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer


@dataclass
class Region:
    name: str
    start_time: int
    end_time: int
    core_id: int
    core_type_id: int
    region_type_id: int
    pipe_type_id: int
    warp_id: int


class RegionTracingAnalyzerBase(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()

    def insert_region(self, region: Region) -> None:
        try:
            database_handler.execute(
                """
                INSERT INTO RegionNames
                (RegionName)
                VALUES(?)
            """,
                (region.name,),
            )
            region_name_id = database_handler.get_lastrowid()
        except sqlite3.IntegrityError:  # Region name already exists
            region_name_id = database_handler.execute(
                """
                SELECT RegionNameId
                FROM RegionNames
                WHERE RegionName=:name
                """,
                {"name": region.name},
            ).fetchone()[0]

        database_handler.execute(
            """
            INSERT INTO Regions
                (RegionNameId, RegionStart, RegionEnd,
                 RegionCoreId, CoreTypeId, RegionTypeId,
                 InstrQueueTypeId, WarpId)
            VALUES
                (:region_name_id, :region_start, :region_end,
                 :region_core_id, :core_type_id, :region_type_id,
                 :pipe_type_id, :warp_id)
        """,
            {
                "region_name_id": region_name_id,
                "region_start": region.start_time,
                "region_end": region.end_time,
                "region_core_id": region.core_id,
                "core_type_id": region.core_type_id,
                "region_type_id": region.region_type_id,
                "pipe_type_id": region.pipe_type_id,
                "warp_id": region.warp_id,
            },
        )

    def get_unique_cores(self) -> List[sqlite3.Row]:
        active = BackendDBManager.get_active_cores()
        result: List[sqlite3.Row] = []
        for core_id in active:
            row = database_handler.execute(
                """
            SELECT :id AS Id
            """,
                {"id": core_id},
            ).fetchone()
            if row is not None:
                result.append(row)
        return result

    def get_unique_core_types(self, core: int) -> List[sqlite3.Row]:
        unique_core_types = database_handler.execute(
            """
        SELECT DISTINCT CoreTypeId AS Id
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId=:core
        """,
            {"core": core},
        )
        result: List[sqlite3.Row] = unique_core_types.fetchall()
        return result
