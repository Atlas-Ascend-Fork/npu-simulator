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

from npusim.prof.src.common.biprof_enum import ArchName
from npusim.prof.src.arch_diagram.view import ArchDiagramView
from npusim.prof.src.arch_diagram.views import Views
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.logger import final_logger

# supported views for David V100
MemoryViewSimGbs2Cores = ArchDiagramView(Views.AICORE_MEMORY_SIM_GBS_2_VEC_CORES.value)
MemoryViewSimRatio2Cores = ArchDiagramView(Views.AICORE_MEMORY_SIM_RATIO_2_VEC_CORES.value)
MemoryViewSimCount2Cores = ArchDiagramView(Views.AICORE_MEMORY_SIM_CNT_2_VEC_CORES.value)


class ArchDiagramViewSet:
    VIEWSET = {
        ArchName.V100: [
            MemoryViewSimGbs2Cores,
            MemoryViewSimRatio2Cores,
            MemoryViewSimCount2Cores,
        ],
    }

    def __init__(self, arch: str, veccore_cnt: int) -> None:
        self._arch: str = arch
        self._veccore_cnt: int = veccore_cnt
        self._viewset: list = self.VIEWSET[ArchName(arch)]

    def debug_print_views(self) -> None:
        if self._viewset is None:
            log.debug(
                "No arch diagram views for " + self._arch + " (" + str(self._veccore_cnt) + " core(s)) architecture"
            )
            return
        log.debug("Arch diagram view set for " + self._arch + " (" + str(self._veccore_cnt) + " core(s)) architecture")
        for view in self._viewset:
            log.debug(f"View name: {view}")
            log.debug(f"View template: {view.get_svg_template_path(self._arch)}")
            view.debug_print_groups()

    def process_data(self, input_dir: str) -> int:
        ret = 0
        for view in self._viewset:
            ret += view.process_data(input_dir)
        return ret

    def generate_diagrams(self, output_dir: str) -> int:
        ret = 0
        with StageReporter("Architecture Diagram generation"):
            for view in self._viewset:
                ret += view.generate_diagram(self._arch, output_dir)

        final_logger.add_general_info(f"Generated Architecture Diagrams: {output_dir}")
        return ret
