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

from bisect import bisect_left
from dataclasses import replace
from typing import List, Dict, Tuple, Optional, Sequence

from npusim.prof.src.common.biprof_enum import ArchName
from npusim.core.public.aicore_objects import DeviceObject
from npusim.prof.src.common.biprof_context import biprof_ctx
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
        self._elf_functions: Optional[List[FunctionInfo]] = None

    def read_object_functions(self) -> Tuple[List[FunctionInfo], Dict[str, int]]:
        if self._elf_functions is None:
            self._elf_functions = self.object_reader.read_functions_info(self._obj_file_name)
        functions = list(self._elf_functions)
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

    def read(self, kernel_name: str, executed_pcs: Optional[Sequence[int]] = None) -> ProcessedObjData:
        additional_symbols = []
        if biprof_ctx.kernel_name and executed_pcs is not None:
            if self._elf_functions is None:
                self.read_object_functions()
            pcs = sorted(set(executed_pcs))
            for func in self._elf_functions:
                index = bisect_left(pcs, func.addr)
                if index < len(pcs) and pcs[index] < func.addr + func.size:
                    additional_symbols.append(func.name)
        return self.object_reader.read(self._obj_file_name, kernel_name, additional_symbols=additional_symbols)

    def read_for_launch(
        self, selected: DeviceObject, executed_pcs: Sequence[int]
    ) -> Tuple[ProcessedObjData, List[FunctionInfo]]:
        """Translate ELF data once; all DB consumers keep launch-relative PCs."""
        functions, _ = self.read_object_functions()
        data = self.read(selected.kernel, executed_pcs=[selected.elf_pc(pc) for pc in executed_pcs])
        loaded_pcs = sorted({instr.addr for instr in data.instructions})
        relevant = []
        for func in functions:
            index = bisect_left(loaded_pcs, func.addr)
            if index < len(loaded_pcs) and loaded_pcs[index] < func.addr + func.size:
                relevant.append(replace(func, addr=selected.record_pc(func.addr)))
        return replace(
            data,
            instructions=[replace(instr, addr=selected.record_pc(instr.addr)) for instr in data.instructions],
            basic_blocks=[replace(block, addr=selected.record_pc(block.addr)) for block in data.basic_blocks],
        ), relevant
