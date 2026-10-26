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

import sqlite3

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.extend_params import ExtendParams

ldg_type_map = {0: "u8", 1: "s8", 2: "u16", 3: "s16", 4: "b32", 5: "b64", 6: "b128"}

stg_type_map = {0: "b8", 1: "b16", 2: "b32", 3: "b64", 4: "b128"}


class MemoryAnalyzerBase(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()

    @classmethod
    def match_type(cls, instr: sqlite3.Row) -> str:
        name = instr["ExecInstrName"]
        assert name in ["SIMT_STG", "SIMT_LDG", "SIMT_STS", "SIMT_LDS"]
        ep = ExtendParams.parse(instr["ExtendParams"])
        access_btype = ep.access_btype
        if access_btype is not None:
            return access_btype.lower()
        raise RuntimeError(f"Cannot extract access_btype from ExtendParams for instruction {name}")
