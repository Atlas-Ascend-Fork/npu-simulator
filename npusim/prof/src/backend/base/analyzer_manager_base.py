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

from typing import List, OrderedDict, Type
import traceback
import logging as log
import os

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.exceptions import RequirementsIncomplete
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.utils import (
    Timer,
    is_in_pyinstaller_env,
    get_pyinstaller_path,
)
from npusim.prof.src.common.stage_reporter import StageReporter


class AnalyzerManagerBase:
    def __init__(self) -> None:
        self.analysis_order: List[IAnalyzer] = []
        self.supported_analyses: List[Type[IAnalyzer]] = []

    def add_analyzer(self, analyzer: IAnalyzer) -> None:
        self.analysis_order.append(analyzer)

    @staticmethod
    def save_analysis_state(analysis_name: str, analysis_passed: bool) -> None:
        database_handler.execute(
            """
        INSERT INTO AnalysisState
        (AnalysisName,
            AnalysisPassed)
            VALUES
            (:analysis_name,
                :analysis_passed)
        """,
            {"analysis_name": analysis_name, "analysis_passed": analysis_passed},
        )

    def run_analyzers(self) -> None:
        for analyzer in self.analysis_order:
            name = analyzer.get_name()
            status = True
            with StageReporter(f"Executing built-in analyzer {name}"):
                try:
                    analyzer.check_requirements()
                    timer = Timer()
                    analyzer.process()
                except RequirementsIncomplete as err:
                    if biprof_ctx.strict:
                        raise err
                    status = False
                    log.warning(f"{name} requirements are not met. {err.get_error_message()}")
                except Exception as err:
                    if biprof_ctx.strict:
                        raise err
                    status = False
                    log.warning(f"{name} analysis failed: {err}")
                    log.debug(traceback.format_exc())
                else:
                    log.debug(f"{name} analysis completed successfully ({timer.count():.3f} s)")
                finally:
                    self.save_analysis_state(name, status)
                    analyzer.cleanup()
                    database_handler.commit()

    def register_analyzers(self, analysis_list="all") -> int:
        base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..")
        if is_in_pyinstaller_env():
            base_path = get_pyinstaller_path()
        with open(os.path.join(base_path, "sql/analysis.sql"), "r") as file:
            database_handler.execute_script(file.read())
        analysis_map = OrderedDict((analyzer.get_name(), analyzer) for analyzer in self.supported_analyses)
        analysis_names: List[str] = []
        if analysis_list == "all":
            analysis_names = [analyzer.get_name() for analyzer in self.supported_analyses]
        else:
            analysis_names = analysis_list.split(",")
        if biprof_ctx.disable_analyzers:
            disabled_analyzers = biprof_ctx.disable_analyzers.split(",")
        else:
            disabled_analyzers = []
        for name in analysis_names:
            if name not in analysis_map:
                log.error(f"Invalid analysis name - {name}")
                return 1
            if name in disabled_analyzers:
                log.debug(f"Skip disabled '{name}' analysis")
                continue
            analyzer = analysis_map[name]()
            self.add_analyzer(analyzer)
        return 0
