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

import abc
import logging as log
import os
import shutil
import traceback
from typing import List, Optional

from npusim.prof.src.common.biprof_enum import AnalysisType, BiprofMode
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.logger import final_logger
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.args import BiProfArgParser
from npusim.prof.src.backend.base.data_processors_base import IBaseReporter
from npusim.prof.src.backend.reporters import HotspotsDbOnlyReporter


class IBiProfBaseMode(abc.ABC):
    @abc.abstractmethod
    def init(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def run(self) -> int:
        raise NotImplementedError


class BiProfReportMode(IBiProfBaseMode):
    def __init__(self):
        self._launcher = None

    def init(self) -> int:
        self._launcher = BiProfReportSimu()
        ret = self._launcher.init()
        if ret:
            return ret
        return 0

    def run(self) -> int:
        ret = self._launcher.run()
        return ret if isinstance(ret, int) else 1


class IBiProfLauncher(abc.ABC):
    def __init__(self) -> None:
        self._analysis_name = biprof_ctx.analysis_type

    @abc.abstractmethod
    def init(self) -> int:
        pass

    @abc.abstractmethod
    def run(self) -> int:
        pass


class IBiProfReportLauncher(IBiProfLauncher):
    def __init__(self) -> None:
        super().__init__()
        self._reporter: Optional[IBaseReporter] = None

    @abc.abstractmethod
    def get_reporter(self) -> Optional[IBaseReporter]:
        pass

    def init(self) -> int:
        self._reporter = self.get_reporter()
        if self._reporter is None:
            return 1
        return self._reporter.init()

    def run(self) -> int:
        if self._reporter is None:
            return 1
        ret = self._reporter.process()
        if ret != 0:
            log.error("Report generation failed!")
        return ret


class BiProfReportSimu(IBiProfReportLauncher):
    def get_reporter(self) -> Optional[IBaseReporter]:
        reporter: Optional[IBaseReporter] = None
        if self._analysis_name == AnalysisType.HOTSPOTS:
            reporter = HotspotsDbOnlyReporter()
        else:
            log.critical(f"Simulation report is supported only for {AnalysisType.HOTSPOTS} analysis type")
            return None
        final_logger.add_general_info(f"Analysis type: {self._analysis_name}")
        return reporter


class BiProfRunner:
    @staticmethod
    def _get_mode() -> Optional[IBiProfBaseMode]:
        mode = biprof_ctx.mode
        if mode == BiprofMode.REPORT:
            return BiProfReportMode()
        log.critical("Unrecognized BiProf mode!")
        return None

    @staticmethod
    def init(input_args: List[str]) -> int:
        arg_parser = BiProfArgParser()
        args = arg_parser.parse(input_args)

        if args is None:
            return 1

        result_name = "results"
        log.basicConfig(level=log.__dict__[args.log_level.upper()])
        if args.mode == BiprofMode.REPORT:
            args.output_dir = os.path.abspath(os.path.join(args.output, result_name))
            if os.path.exists(args.output_dir):
                shutil.rmtree(args.output_dir)
            os.makedirs(args.output_dir)
            fh = log.FileHandler(f"{args.output_dir}/{os.path.basename(__file__)}.log")
            fh.setLevel(log.DEBUG)
            root = log.getLogger()
            root.addHandler(fh)
            # 所有日志只写文件，不打印到控制台；用户只感知成功/失败
            for h in root.handlers:
                if isinstance(h, log.StreamHandler) and not isinstance(h, log.FileHandler):
                    root.removeHandler(h)
        biprof_ctx.init(args)
        return 0

    @classmethod
    def run(cls, args):
        ret = cls.init(args)
        try:
            with StageReporter("BiProf", hidden=True):
                if ret != 0:
                    return ret
                with StageReporter("BiProf initialization"):
                    biprof = cls._get_mode()
                    if biprof is None:
                        return 1
                    ret = biprof.init()
                    if ret != 0:
                        return ret
                ret = biprof.run()
                final_logger.report(ret)
            StageReporter.dump_perf_table()
            return ret
        except Exception as e:
            if biprof_ctx.log_level.upper() == log.getLevelName(log.DEBUG):
                log.error(traceback.format_exc())
            else:
                log.error(f"biprof failed with the following error: {e}")
            return 1
