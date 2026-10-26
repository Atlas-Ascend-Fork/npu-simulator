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

from typing import List, Dict, Tuple

from npusim.prof.src.common.biprof_enum import ArchName
from npusim.prof.src.backend.elf_object_reader import (
    ElfObjectReader,
    FunctionInfo,
    ProcessedObjData,
)


class ObjectProcessor:
    def __init__(self, arch: ArchName, workspace: str, obj_file_name: str) -> None:
        self._arch = arch
        self._workspace = workspace
        self._obj_file_name = obj_file_name
        self.object_reader = ElfObjectReader(arch, workspace)

    def read_object_functions(self) -> Tuple[List[FunctionInfo], Dict[str, int]]:
        functions = self.object_reader.read_functions_info(self._obj_file_name)
        hidumper_funcs = self.object_reader.read_hidumper_detected_functions_info(self._obj_file_name)
        functions.extend(hidumper_funcs)
        # Stores kernel_name -> addr
        stripped_kernel_names: Dict[str, int] = {}

        for func in functions:
            if func.is_kernel:
                name, addr = func.name, func.addr
                stripped_name = name
                if name.endswith("_mix_aic") or name.endswith("_mix_aiv"):
                    stripped_name = name[: -len("_mix_ai*")]
                if stripped_name not in stripped_kernel_names:
                    stripped_kernel_names[stripped_name] = addr
                else:
                    stripped_kernel_names[stripped_name] = min(addr, stripped_kernel_names[stripped_name])
        return functions, stripped_kernel_names

    def read(self, kernel_name: str) -> ProcessedObjData:
        return self.object_reader.read(self._obj_file_name, kernel_name)
