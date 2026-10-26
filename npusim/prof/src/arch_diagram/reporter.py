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

from npusim.prof.src.backend.base.data_processors_base import IBaseReporter
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import CoreType
from npusim.prof.src.arch_diagram.viewset import ArchDiagramViewSet


class ArchDiagramReporter(IBaseReporter):
    def __init__(self, output_path: str) -> None:
        super().__init__()
        self._input_dir: str = ""
        self._output_dir: str = output_path
        self._arch: str = biprof_ctx.arch
        self._veccore_cnt: int = self.obtain_veccore_cnt()

    def init(self) -> int:
        ret = super().init()
        return ret

    def process(self) -> int:
        processor = ArchDiagramProcessor(self._input_dir, self._output_dir, self._arch, self._veccore_cnt)
        return processor.process()

    def obtain_veccore_cnt(self) -> int:
        sql_query = "SELECT DISTINCT CoreTypeId from ExecutedInstructions"
        core_type_list = database_handler.execute(sql_query).fetchall()
        r = [i["CoreTypeId"] for i in core_type_list]
        if CoreType.AIV1.value in r:
            return 2
        return 1


class ArchDiagramProcessor:
    def __init__(self, input_dir: str, output_dir: str, arch: str, veccore_cnt: int) -> None:
        self._input_dir: str = input_dir
        self._output_dir: str = output_dir
        self._arch: str = arch
        self._veccore_cnt = veccore_cnt

    def process(self) -> int:
        view_set = ArchDiagramViewSet(self._arch, self._veccore_cnt)
        ret = view_set.process_data(self._input_dir)
        ret += view_set.generate_diagrams(self._output_dir)
        return ret
