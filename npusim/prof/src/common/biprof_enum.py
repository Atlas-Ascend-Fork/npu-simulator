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
from enum import Enum, auto
from typing import Dict, List


class ArchName(str, Enum):
    V100 = "v100"

    def __str__(self) -> str:
        return str(self.value)


user_visible_archs = list(ArchName)


class InstrUnit(Enum):
    SCALAR = auto()
    VEC = auto()
    CUBE = auto()
    MTE = auto()
    FIXP = auto()
    SIMT = auto()
    ALL = 50
    NOT_AVAILABLE = auto()

    def __str__(self) -> str:
        return str(self.name)


class InstrQueueType(Enum):
    SCALAR = auto()
    CUBE = auto()
    FIXP = auto()
    MTE1 = auto()
    MTE2 = auto()
    MTE3 = auto()
    SIMT = auto()
    SIMD = auto()
    NONE = auto()


class MemoryType(Enum):
    # cannsim_helper uses 1 for its shared UB scope and 2-5 for L0A/L0B/L0C/L1.
    # 101 and 102 are internal placeholders for legacy names, not helper IDs.
    AIV0_UB = 1
    AIC_L0A = 2
    AIC_L0B = 3
    AIC_L0C = 4
    AIC_L1 = 5
    AIV1_UB = 101
    OUT = 102
    UB = 1  # alias of the helper's shared UB scope


class CTSectionType(Enum):
    PRE = auto()
    INSTR = auto()
    POST = auto()

    def __str__(self) -> str:
        return str(self.name)


def queue_type_to_instr_types() -> Dict[InstrQueueType, List["InstrType"]]:
    return {
        InstrQueueType.SIMD: [
            InstrType.RVECEX,
            InstrType.RVECLP,
            InstrType.RVECSU,
            InstrType.RVECST,
            InstrType.RVECLD,
        ],
        InstrQueueType.SCALAR: [InstrType.SCALAR, InstrType.FC, InstrType.PUSHQ],
        InstrQueueType.CUBE: [InstrType.CUBE],
        InstrQueueType.MTE1: [InstrType.MTE1],
        InstrQueueType.MTE2: [InstrType.MTE2],
        InstrQueueType.MTE3: [InstrType.MTE3],
        InstrQueueType.FIXP: [InstrType.FIXP],
        InstrQueueType.SIMT: [
            InstrType.RVECEX,
            InstrType.RVECLP,
            InstrType.RVECSU,
            InstrType.RVECST,
            InstrType.RVECLD,
        ],
    }


class BiprofMode(str, Enum):
    REPORT = "report"

    def __str__(self) -> str:
        return str(self.value)


class AnalysisType(str, Enum):
    HOTSPOTS = "hotspots"

    def __str__(self) -> str:
        return str(self.value)


class CoreType(Enum):
    AIC = auto()
    AIV0 = auto()
    AIV1 = auto()

    def __format__(self, format_spec: str) -> str:
        return f"{self.name:{format_spec}}"


class LogLevel:
    CRITICAL = "critical"
    DEBUG = "debug"
    ERROR = "error"
    INFO = "info"
    NOTSET = "notset"
    WARNING = "warning"


class ArgumentAction:
    STORE = "store"
    STORE_TRUE = "store_true"
    STORE_FALSE = "store_false"
    STORE_CONST = "store_const"
    APPEND = "append"
    APPEND_CONST = "append_const"
    COUNT = "count"
    HELP = "help"
    VERSION = "version"
    EXTEND = "extend"


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
    ALL = 99
    NOT_AVAILABLE = 100

    def __str__(self) -> str:
        return self.name

    def __format__(self, format_spec: str) -> str:
        return f"{self.name:{format_spec}}"


class UbAccessType(Enum):
    SIMT_RD = auto()
    SIMT_WR = auto()
    SIMD_RD = auto()
    SIMD_WR = auto()


class SubQueueType(Enum):
    EXEC = auto()
    FLAG = auto()
    PUSH_PB = auto()


class ICacheEventType(Enum):
    SCALAR = auto()
    VECTOR = auto()

    def __str__(self) -> str:
        return self.name


class BrifEventType(Enum):
    SEND_CMD_EVENT = auto()
    RECV_DATA_EVENT = auto()
    PUSH_DATA_EVENT = auto()

    def __str__(self) -> str:
        return self.name


@dataclass
class BrifEvent:
    core_id: int
    event_type: int
    event_tick: int
    mte_instr_id: int
    addr: str
    tag_id: int
    group_id: int
