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

import logging as log
import os
import re
from typing import List, Optional

import npusim.prof.src.common.utils as utils
from npusim.core.public.comm import print_error, print_info
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.base.data_processors_base import IBaseReporter
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzer_manager import AnalyzerManager
from npusim.prof.src.backend.hint_manager import HintManager
from npusim.prof.src.backend.hotspots_processor import HotspotsProcessor
from npusim.prof.src.backend.object_processor import ObjectProcessor
from npusim.prof.src.backend.database_manager import BackendDBManager
from npusim.prof.src.backend.vf_export import export_vf_report_json


class HotspotsReporterBase(IBaseReporter):
    def clear_tmp_files(self) -> None:
        for file in utils.get_files_by_suffix(self._output_dir, ".db"):
            os.remove(os.path.join(self._output_dir, file))

    @staticmethod
    def get_current_db_stem() -> str:
        db_path = database_handler.get_db_path()
        if db_path is None:
            return "vf_report"
        return os.path.splitext(os.path.basename(db_path))[0]


class HotspotsDbOnlyReporter(HotspotsReporterBase):
    def __init__(self) -> None:
        super().__init__()
        self._input_db: str = ""
        self._input_dbs: List[str] = []

    def init(self) -> int:
        ret = super().init()
        self._input_db = os.path.abspath(biprof_ctx.input_db)
        if os.path.isdir(self._input_db):
            self._input_dbs = sorted(
                os.path.join(self._input_db, name) for name in os.listdir(self._input_db) if name.endswith(".db")
            )
        elif os.path.isfile(self._input_db):
            self._input_dbs = [self._input_db]

        if not self._input_dbs:
            print_error(f"No contract DB files found in input-db: {self._input_db}")
            return 1
        return ret

    @staticmethod
    def _safe_report_component(value: str) -> str:
        value = value.strip() or "kernel"
        value = re.sub(r"[^\w.\-+=:@]+", "_", value)
        return value[:240] or "kernel"

    @staticmethod
    def _get_kernel_info_attr(attr: str) -> Optional[str]:
        if not database_handler.table_exists("KernelInfo"):
            return None
        row = database_handler.execute(
            """
            SELECT KernelInfoVal
            FROM KernelInfo
            WHERE KernelInfoAttr=:attr
            """,
            {"attr": attr},
        ).fetchone()
        if row is None:
            return None
        return str(row["KernelInfoVal"])

    @classmethod
    def _get_kernel_name(cls, input_db: str) -> str:
        name = cls._get_kernel_info_attr("name")
        if name:
            return name
        return os.path.splitext(os.path.basename(input_db))[0]

    @classmethod
    def _get_launch_id(cls, index: int) -> int:
        launch_id = cls._get_kernel_info_attr("launch_id")
        if not launch_id:
            return index
        try:
            return int(launch_id)
        except ValueError:
            return index

    def _process_one_db(self, input_db: str, index: int) -> int:
        ret = 0
        database_handler.connect(input_db)
        try:
            database_handler.create_tables(self._analysis_name)
            self._arch = database_handler.get_arch() or self._arch
            if biprof_ctx.arch is None and self._arch is not None:
                biprof_ctx.arch = self._arch
            BackendDBManager.fill_static_tables()

            kernel_name = self._get_kernel_name(input_db)
            launch_id = self._get_launch_id(index)
            report_kernel_name = self._safe_report_component(kernel_name)
            report_dir_name = f"{launch_id:04d}_{report_kernel_name}_reports"

            object_file = biprof_ctx.object_file
            if object_file:
                obj_proc = ObjectProcessor(self._arch, self._output_dir, object_file)
                functions, _ = obj_proc.read_object_functions()
                obj_data = obj_proc.read(kernel_name)
                with StageReporter("Save processed object info"):
                    BackendDBManager.save_processed_data(obj_data, functions)

            processor = HotspotsProcessor(
                arch=self._arch,
                workspace=self._output_dir,
                log_folder=os.path.join(
                    self._output_dir,
                    f"kernel_launch_{launch_id}",
                ),
                log_files=[],
                object_path=object_file,
                kernel_name=kernel_name,
                kernel_name_short=report_kernel_name,
                report_dir_name=report_dir_name,
            )

            analyzer = AnalyzerManager()
            analyzer.register_analyzers()
            analyzer.run_analyzers()

            hinter = HintManager()
            hinter.register_hints()
            hinter.run_hints()

            ret += processor.generate_chrome_tracing()
            if biprof_ctx.vf_report_json:
                export_vf_report_json(
                    database_handler,
                    processor.report_dir_path,
                    self.get_current_db_stem(),
                )
            if biprof_ctx.bubble:
                ret += processor.generate_bubble_report()
            ret += processor.generate_arch_diagram()

            ret += processor.generate_summary_report()
        finally:
            database_handler.disconnect()
        return ret

    def process(self) -> int:
        ret = 0
        print_info(f"Processing {len(self._input_dbs)} contract DB file(s).")
        for index, input_db in enumerate(self._input_dbs):
            if index > 0:
                StageReporter.separate()
            ret += self._process_one_db(input_db, index)

        if not biprof_ctx.save_tmp_files:
            self.clear_tmp_files()
        if ret != 0:
            print_error("Report generation failed!")
            log.error("Report generation failed!")
        return ret
