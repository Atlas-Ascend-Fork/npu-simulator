# -*- coding: utf-8 -*-
#!/usr/bin/env python3
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

from enum import Enum, auto


class InstrQueueType(Enum):
    SCALAR = auto()
    CUBE = auto()
    FIXP = auto()
    MTE1 = auto()
    MTE2 = auto()
    MTE3 = auto()
    SIMT = auto()
    SIMD = auto()


class CTSectionType(Enum):
    INSTR = auto()

    def __str__(self) -> str:
        return str(self.name)


class CoreType(Enum):
    AIC = auto()
    AIV0 = auto()
    AIV1 = auto()

    def __format__(self, format_spec: str) -> str:
        return f"{self.name:{format_spec}}"


class InstrType(Enum):
    SCALAR = auto()
    CUBE = auto()
    MTE1 = auto()
    MTE2 = auto()
    MTE3 = auto()
    FC = auto()
    FIXP = auto()
    RVECEX = 50
    RVECLP = auto()
    RVECSU = auto()
    RVECST = auto()
    RVECLD = auto()
    PUSHQ = auto()

    def __str__(self) -> str:
        return self.name

    def __format__(self, format_spec: str) -> str:
        return f"{self.name:{format_spec}}"
