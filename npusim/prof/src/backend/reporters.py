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
from pathlib import Path

from npusim.core.public.aicore_objects import DeviceObjectResolver

import npusim.prof.src.common.utils as utils
from npusim.core.public.comm import print_error, print_info, print_warn
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
        saved_context = {
            key: biprof_ctx.__dict__[key] for key in ("object_file", "kernel_name") if key in biprof_ctx.__dict__
        }
        database_handler.connect(input_db)
        try:
            # Warmup / 空 kernel 的 DB 没有任何执行指令（如 VF profiling 样例的
            # warmup_kernel），后续分析会因 exec_time_ticks=0 除零。直接跳过，
            # 避免一个空 DB 拖垮整次 report。
            if (
                not database_handler.table_exists("ExecutedInstructions")
                or database_handler.count_rows("ExecutedInstructions") == 0
            ):
                print_info(f"Skip empty kernel DB (no executed instructions): {input_db}")
                log.info("skip empty kernel DB: %s", input_db)
                return 0

            database_handler.create_tables(self._analysis_name)
            self._arch = database_handler.get_arch() or self._arch
            if biprof_ctx.arch is None and self._arch is not None:
                biprof_ctx.arch = self._arch
            BackendDBManager.fill_static_tables()

            kernel_name = self._get_kernel_name(input_db)
            launch_id = self._get_launch_id(index)
            report_kernel_name = self._safe_report_component(kernel_name)
            report_dir_name = f"{launch_id:04d}_{report_kernel_name}_reports"

            trace = database_handler.execute(
                "SELECT DISTINCT SourceInstrAddr, SourceInstrEncoding FROM ExecutedInstructions"
            ).fetchall()
            selected = self._object_resolver.select(kernel_name, executed_instructions=trace)
            object_file = selected.path if selected else None
            device_kernel = selected.kernel if selected else kernel_name
            # Re-reporting a DB must not reuse a previous object or duplicate its
            # source rows. These tables are derived, never the recorded trace.
            for table in (
                "CallStackIDs",
                "BasicBlocks",
                "Functions",
                "SourceInstructions",
                "DebugInfo",
                "SourceLines",
                "SourceFiles",
                "Sections",
            ):
                database_handler.execute(f"DELETE FROM {table}")
            database_handler.execute(
                "DELETE FROM sqlite_sequence WHERE name IN ('Sections', 'SourceFiles', 'SourceLines', 'DebugInfo')"
            )
            biprof_ctx.object_file = object_file
            biprof_ctx.kernel_name = device_kernel
            database_handler.execute("DELETE FROM KernelInfo WHERE KernelInfoAttr='object_entry_pc'")
            if object_file:
                try:
                    obj_proc = ObjectProcessor(self._arch, self._output_dir, object_file)
                    obj_data, functions = obj_proc.read_for_launch(
                        selected, executed_pcs=sorted({row[0] for row in trace})
                    )
                except Exception as error:
                    print_warn(f"Cannot read AI Core object {object_file} for kernel {kernel_name!r}: {error}")
                    log.debug("AI Core object read failed", exc_info=True)
                    object_file = biprof_ctx.object_file = None
                    biprof_ctx.kernel_name = kernel_name
                else:
                    database_handler.execute(
                        "INSERT INTO KernelInfo (KernelInfoAttr, KernelInfoVal) VALUES ('object_entry_pc', ?)",
                        (selected.entry_pc,),
                    )
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

            with StageReporter("Save full source lines"):
                BackendDBManager.save_full_source_lines()
                # Tracing workers reconnect to the DB; publish the snapshot first.
                database_handler.commit()

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
            for key in ("object_file", "kernel_name"):
                biprof_ctx.__dict__.pop(key, None)
            biprof_ctx.__dict__.update(saved_context)
            database_handler.disconnect()
        return ret

    def process(self) -> int:
        ret = 0
        print_info(f"Processing {len(self._input_dbs)} contract DB file(s).")
        source = Path(self._input_db)
        self._object_resolver = DeviceObjectResolver(
            source.parent if source.is_file() else source, biprof_ctx.object_file
        )
        try:
            for index, input_db in enumerate(self._input_dbs):
                if index > 0:
                    StageReporter.separate()
                ret += self._process_one_db(input_db, index)
        finally:
            self._object_resolver.close()

        if not biprof_ctx.save_tmp_files:
            self.clear_tmp_files()
        if ret != 0:
            print_error("Report generation failed!")
            log.error("Report generation failed!")
        return ret
