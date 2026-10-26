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
import re
from typing import List

from npusim.prof.src.backend.database_manager import BackendDBManager


class IAnalyzer(abc.ABC):
    def check_requirements(self) -> None:
        return

    def process(self) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        pass

    @staticmethod
    def get_name() -> str:
        raise NotImplementedError

    @staticmethod
    def get_dtype_size(dtype: str) -> int:
        dtype_map = {
            "b8": 1,
            "s8": 1,
            "u8": 1,
            "b16": 2,
            "f16": 2,
            "s16": 2,
            "u16": 2,
            "bf16": 2,
            "fmix": 2,
            "b32": 4,
            "b32s": 4,
            "s32": 4,
            "f32": 4,
            "u32": 4,
            "s162s8": 2,
            "f162s8": 2,
            "f322f16": 4,
            "VDEQs162b8": 2,
            "f162u8": 2,
            "b64": 8,
            "b128": 16,
        }
        return dtype_map[dtype.lower()]

    @staticmethod
    def extract_str_value(pattern: str, string: str) -> str:
        val = re.compile(pattern).search(string)
        if val is None:
            raise RuntimeError(f"Unable to find pattern {pattern} in a string {string}")
        return val[1]

    @staticmethod
    def extract_int_value(pattern: str, string: str, base: int = 10) -> int:
        return int(IAnalyzer.extract_str_value(pattern, string), base=base)

    @staticmethod
    def get_active_cores() -> List[int]:
        return BackendDBManager.get_active_cores()

    @staticmethod
    def get_active_core_types() -> List[int]:
        return BackendDBManager.get_active_core_types()
