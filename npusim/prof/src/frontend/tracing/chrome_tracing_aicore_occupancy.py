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
import dataclasses
import json
import logging as log
import os
from collections import defaultdict
from typing import Any, Dict, List, Set
from npusim.prof.src.common.utils import get_tick_to_us_coeff
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.frontend.tracing.chrome_tracing_default import (
    CHROME_COLOR_RAIL_RESPONSE,
    CHROME_COLOR_RUNNING,
    CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED,
    EVENT_PHASE_METADATA,
    MetaEvent,
)
from npusim.prof.src.common.stage_reporter import StageReporter


@dataclass
class OccupancyEventArgs:
    utilization: str


@dataclass
class OccupancyEvent:
    name: str  # Core type name (for example: AIV0)
    pid: int  # process id (for example: CoreId)
    tid: int  # = CoreTypeId
    ts: int  # Event start time
    dur: int  # Event duration
    cname: str  # Event color
    args: OccupancyEventArgs
    ph: str = "X"


class DatabaseManager:
    @staticmethod
    def get_occupancy_events() -> List[Any]:
        events: List[Any] = []
        items = database_handler.execute(
            """
            SELECT CoreId, AiCoreOccupancy.CoreTypeId, CoreTypeName, TickStart, TickEnd
            FROM AiCoreOccupancy
            LEFT JOIN CoreTypes
            ON AiCoreOccupancy.CoreTypeId = CoreTypes.CoreTypeId
            """
        ).fetchall()
        freq_coeff = get_tick_to_us_coeff()

        start = min(items, key=lambda item: item["TickStart"])["TickStart"]
        end = max(items, key=lambda item: item["TickEnd"])["TickEnd"]
        total_duration_ticks = end - start

        seen_pids: Set[int] = set()
        seen_tids: Dict[int, Set[int]] = defaultdict(set)

        for item in items:
            pid = int(item["CoreId"])
            tid = int(item["CoreTypeId"])
            duration_ticks = item["TickEnd"] - item["TickStart"]
            util_value = duration_ticks / total_duration_ticks * 100
            cname = None

            if item["CoreTypeName"] == "AIV0":
                cname = CHROME_COLOR_RUNNING  # dark green
            elif item["CoreTypeName"] == "AIV1":
                cname = CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED  # green
            else:  # item["CoreTypeName"] == "AIC"
                cname = CHROME_COLOR_RAIL_RESPONSE  # blue

            if pid not in seen_pids:
                events.append(
                    MetaEvent(
                        "process_name",
                        pid,
                        0,
                        EVENT_PHASE_METADATA,
                        {"name": f"Core {pid}"},
                    )
                )
                seen_pids.add(pid)
            if tid not in seen_tids[pid]:
                events.append(
                    MetaEvent(
                        "thread_name",
                        pid,
                        tid,
                        EVENT_PHASE_METADATA,
                        {"name": item["CoreTypeName"]},
                    )
                )
                seen_tids[pid].add(tid)

            events.append(
                OccupancyEvent(
                    name=item["CoreTypeName"],
                    pid=pid,
                    tid=tid,
                    ts=item["TickStart"] * freq_coeff,
                    dur=duration_ticks * freq_coeff,
                    cname=cname,
                    args=OccupancyEventArgs(utilization=f"{round(util_value, 2)}%"),
                )
            )
        return events


class AiCoreOccupancyTraceBuilder:
    @staticmethod
    def generate(output_dir: str) -> int:
        os.makedirs(output_dir, exist_ok=True)
        with StageReporter("Chrome Tracing generation (AiCore Utilization)"):
            events = DatabaseManager.get_occupancy_events()
            json_dict = {
                "traceEvents": [dataclasses.asdict(event) for event in events],
                "displayTimeUnit": "ns",
            }
            try:
                with open(os.path.join(output_dir, "aicore_utilization.json"), "w") as file:
                    json.dump(json_dict, file)
            except Exception as e:
                log.warning(e)
                return 1
            return 0
