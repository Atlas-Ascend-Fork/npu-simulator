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
import os
import logging as log
from typing import Dict
from collections import defaultdict
from npusim.prof.src.arch_diagram.xml_processor import (
    ArchDiagramXmlProcessor as XmlProcessor,
)
from npusim.prof.src.common.utils import is_in_pyinstaller_env, get_pyinstaller_path


@dataclass
class ViewData:
    name: str
    suffix: str
    metric_groups: tuple


class ArchDiagramView:
    def __init__(self, view_data: ViewData):
        self._viewdata: ViewData = view_data
        self._name: str = view_data.name

    def __str__(self) -> str:
        return f"{self._name}"

    def debug_print_groups(self) -> None:
        for group in self._viewdata.metric_groups:
            log.debug(f"  Group name: {group}")
            group.debug_print_metrics()

    def get_svg_template_path(self, arch) -> str:
        if is_in_pyinstaller_env():
            base_path = get_pyinstaller_path()
        else:
            base_path = os.path.join(os.path.dirname(__file__), "..", "..")
        path_to_template = os.path.abspath(
            os.path.join(
                base_path,
                "static",
                "arch_diagrams",
                f"{arch}",
                f"{self._name}_view.svg",
            )
        )
        return path_to_template

    def process_data(self, input_dir) -> int:
        ret = 0
        for group in self._viewdata.metric_groups:
            ret += group.scan_db(input_dir)
        return ret

    def generate_diagram(self, arch: str, output_dir: str) -> int:
        metrics_dict: Dict[str, str] = defaultdict(str)
        for group in self._viewdata.metric_groups:
            metrics_dict = {**metrics_dict, **group.get_metrics_dict()}

        template_file = self.get_svg_template_path(arch)
        xml_processor = XmlProcessor(template_file, output_dir, metrics_dict, self._viewdata.suffix)
        return xml_processor.patch_svg_template()
