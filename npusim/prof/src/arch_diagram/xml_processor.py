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

import os
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import List
from math import isclose


suffix_dict = {
    "gbs": "bandwidth_per_operator",
    "cnt": "number_of_requests",
    "ratio": "bandwidth_per_request",
}


class ArchDiagramXmlProcessor:
    G_TAG = "g"
    PATH_TAG = "path"
    TEXT_TAG = "text"
    TSPAN_TAG = "tspan"

    def __init__(self, template: str, output: str, metrics: dict, suffix: str) -> None:
        self._template: str = template
        self._output: str = output
        self._metrics: dict = metrics
        self._suffix: str = suffix
        self._tree = ET.parse(self._template)  # nosec B314
        self._root = self._tree.getroot()

    @staticmethod
    def get_nodes_by_name(node: ET.Element, tag: str, result: list) -> None:
        if node is not None:
            for item in node.findall("*"):
                tag_elements = item.tag.split("}")
                # get real tag following namespace string:
                # {http://www.w3.org/2000/svg}text
                real_tag = tag_elements[1]
                if real_tag == tag:
                    result.append(item)
                ArchDiagramXmlProcessor.get_nodes_by_name(item, tag, result)

    def patch_svg_template(self) -> int:
        self.process_counters()
        self.process_arrows()
        self.write_tree_to_file()
        return 0

    def process_counters(self) -> None:
        all_text_nodes: List[ET.Element] = list()
        self.get_nodes_by_name(self._root, self.TEXT_TAG, all_text_nodes)
        ns = ".//{http://www.w3.org/2000/svg}"
        for node in all_text_nodes:
            for tspan in node.findall(f"{ns}{self.TSPAN_TAG}"):
                metric_name = str(tspan.text)
                if metric_name.endswith("_"):
                    metric_name += self._suffix
                if metric_name in self._metrics:
                    # save metric text from template to an id attribute
                    # and rewrite it with formatted value
                    tspan.set("id", metric_name)
                    tspan.text = self._metrics[metric_name].formatted_value

    def process_arrows(self) -> None:
        all_g_nodes: List[ET.Element] = list()
        all_group_nodes: List[ET.Element] = list()
        self.get_nodes_by_name(self._root, self.G_TAG, all_g_nodes)
        for node in all_g_nodes:
            if str(node.get("id")).startswith("group"):
                all_group_nodes.append(node)

        for group in all_group_nodes:
            tspan_nodes: List[ET.Element] = list()
            path_nodes: List[ET.Element] = list()
            self.get_nodes_by_name(group, self.TSPAN_TAG, tspan_nodes)
            self.get_nodes_by_name(group, self.PATH_TAG, path_nodes)
            for tspan in tspan_nodes:
                metric_name = tspan.get("id")
                if metric_name is None or metric_name not in self._metrics:
                    continue
                relative_tp = self._metrics[metric_name].relative_tp
                if not isclose(relative_tp, 0.0):
                    color_code = self.get_tp_color_code(relative_tp)
                    for path in path_nodes:
                        path.set("stroke", color_code)
                        if path.get("fill") != "none":
                            path.set("fill", color_code)

    def write_tree_to_file(self) -> None:
        adjusted_name = f"{Path(self._template).name[:-4]}_{suffix_dict[self._suffix]}.svg"
        path_to_generated_svg = os.path.abspath(os.path.join(self._output, adjusted_name))
        self._tree.write(path_to_generated_svg)

    def get_tp_color_code(self, percent: float) -> str:
        color_code = "#04b7fe"
        if percent > 0.0 and percent < 10.0:
            color_code = "#02b9ff"
        elif percent >= 10.0 and percent < 20.0:
            color_code = "#405ed0"
        elif percent >= 20.0 and percent < 30.0:
            color_code = "#833882"
        elif percent >= 30.0 and percent < 40.0:
            color_code = "#be1d43"
        elif percent >= 40.0 and percent < 50.0:
            color_code = "#ff0a05"
        elif percent >= 50.0 and percent < 60.0:
            color_code = "#ff391d"
        elif percent >= 60.0 and percent < 70.0:
            color_code = "#ff743a"
        elif percent >= 70.0 and percent < 80.0:
            color_code = "#ff9f4f"
        elif percent >= 80.0 and percent < 90.0:
            color_code = "#ffcd67"
        elif percent >= 90.0:
            color_code = "#fffd7f"
        return color_code
