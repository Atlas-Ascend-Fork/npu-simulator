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
"""Legacy unit utilization counters without smart-pipe/dispatch placeholders."""

from npusim.prof_demo.database import database_handler, BackendDBManager


class UnitUtilizationAnalyzer:
    def process(self):
        parts = {1: (2, 7, 3, 4), 2: (4, 5, 55), 3: (4, 5, 55)}
        output = []
        for core in BackendDBManager.get_active_cores():
            for core_type in BackendDBManager.get_active_core_types():
                pipes = parts[core_type]
                placeholders = ",".join("?" for _ in pipes)
                params = (core, core_type, *pipes)
                # Preserve legacy tie order: all starts precede all ends.
                starts = database_handler.execute(
                    f"""
                    SELECT ExecInstrTickStart, InstrTypeId, 0 FROM ExecutedInstructions
                    WHERE ExecInstrCoreId=? AND CoreTypeId=? AND InstrTypeId IN ({placeholders})
                    ORDER BY ExecInstrTickStart
                """,
                    params,
                ).fetchall()
                ends = database_handler.execute(
                    f"""
                    SELECT ExecInstrTickEnd, InstrTypeId, 1 FROM ExecutedInstructions
                    WHERE ExecInstrCoreId=? AND CoreTypeId=? AND InstrTypeId IN ({placeholders})
                    ORDER BY ExecInstrTickEnd
                """,
                    params,
                ).fetchall()
                active = []
                for tick, pipe, position in sorted(starts + ends, key=lambda row: row[0]):
                    if position == 0:
                        active.append(pipe)
                    elif pipe in active:
                        active.remove(pipe)
                    output.append((tick, len(set(active)) / len(pipes), core, core_type))
        database_handler.executemany("INSERT INTO UnitUtilization VALUES (?, ?, ?, ?)", output)
