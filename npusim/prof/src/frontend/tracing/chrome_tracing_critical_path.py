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

from typing import Any, List
from npusim.prof.src.frontend.tracing.chrome_tracing_default import (
    CHROME_COLOR_GREY,
    ChromeTracingBuilder,
    InstrTraceBuilder,
    DatabaseManager,
)
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.biprof_context import biprof_ctx
from concurrent.futures import ProcessPoolExecutor, as_completed
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.logger import final_logger


class DatabaseManagerExtra:
    @staticmethod
    def get_cpath_instr_list() -> List[int]:
        if not database_handler.table_exists("DispatchTime"):
            return []
        cpath_instrs = database_handler.execute("""
        SELECT InstrId
        FROM CriticalPath
        """).fetchall()
        return [int(instr["InstrId"]) for instr in cpath_instrs]


class CriticalPathTraceBuilder(InstrTraceBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.cpath_instr_list = DatabaseManagerExtra.get_cpath_instr_list()
        self.dispatch_events_map = {}
        event_map_copy = dict(self.event_details_map)
        for key in event_map_copy.keys():
            if key not in self.cpath_instr_list:
                self.event_details_map.pop(key)

    def get_dispatch_events(self, pid, pipe, core_id, core_type_id, instr_type_id) -> List[Any]:
        return []

    def get_color(self, instr: Any) -> str:
        if instr["Id"] in self.cpath_instr_list:
            return ""
        return CHROME_COLOR_GREY


class CriticalPathBuilder(ChromeTracingBuilder):
    @staticmethod
    def get_json_obj(core_id: int) -> List[Any]:
        res = CriticalPathTraceBuilder().get_json_obj(core_id)
        return res

    @classmethod
    def generate(cls, output_dir) -> int:
        core_list = DatabaseManager.get_active_core_list()
        with StageReporter("Critical path generation"):
            with ProcessPoolExecutor(biprof_ctx.jobs) as executor:
                futures = [
                    executor.submit(cls.generate_core_tracing, core, output_dir, "critical_path") for core in core_list
                ]
                for future in as_completed(futures):
                    result = future.result()
                    if result != 0:
                        return 1
        final_logger.add_general_info(f"Critical path reports: {output_dir}")
        return 0
