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
from typing import List
from npusim.prof_demo.utils import get_tick_to_us_coeff
from npusim.prof_demo.database import database_handler
from npusim.prof_demo.reports.tracing import (
    CHROME_COLOR_RAIL_RESPONSE,
    CHROME_COLOR_RUNNING,
    CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED,
)


@dataclass
class OccupancyEventArgs:
    utilization: str


@dataclass
class OccupancyEvent:
    name: str  # Core type name (for example: AIV0)
    pid: str  # "process id"   (for example: Core 0)
    tid: str  # = name
    ts: int  # Event start time
    dur: int  # Event duration
    cname: str  # Event color
    args: OccupancyEventArgs
    ph: str = "X"


class DatabaseManager:
    @staticmethod
    def get_occupancy_events() -> List[OccupancyEvent]:
        events = []
        items = database_handler.execute(
            """
            SELECT AiCoreOccupancy.CoreId, CoreVocabulary.CoreTypeName,
                   TickStart, TickEnd
            FROM main.AiCoreOccupancy
            LEFT JOIN (
                SELECT EI.CoreTypeId, MIN(EP.core_type) AS CoreTypeName
                FROM ExecutedInstructions AS EI
                JOIN main.ExtendParamsProjection AS EP
                ON EI.ExecInstrId = EP.ExecInstrId
                GROUP BY EI.CoreTypeId
            ) AS CoreVocabulary
            ON AiCoreOccupancy.CoreTypeId = CoreVocabulary.CoreTypeId
            """
        ).fetchall()
        freq_coeff = get_tick_to_us_coeff()

        start = min(items, key=lambda item: item["TickStart"])["TickStart"]
        end = max(items, key=lambda item: item["TickEnd"])["TickEnd"]
        total_duration_ticks = end - start

        for item in items:
            duration_ticks = item["TickEnd"] - item["TickStart"]
            util_value = duration_ticks / total_duration_ticks * 100
            cname = None

            if item["CoreTypeName"] == "AIV0":
                cname = CHROME_COLOR_RUNNING  # dark green
            elif item["CoreTypeName"] == "AIV1":
                cname = CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED  # green
            else:  # item["CoreTypeName"] == "AIC"
                cname = CHROME_COLOR_RAIL_RESPONSE  # blue

            events.append(
                OccupancyEvent(
                    name=item["CoreTypeName"],
                    pid=f"Core {item['CoreId']}",
                    tid=item["CoreTypeName"],
                    ts=item["TickStart"] * freq_coeff,
                    dur=duration_ticks * freq_coeff,
                    cname=cname,
                    args=OccupancyEventArgs(utilization=f"{round(util_value, 2)}%"),
                )
            )
        return events
