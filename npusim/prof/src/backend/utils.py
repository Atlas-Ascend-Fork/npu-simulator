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

from numbers import Rational
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional
from npusim.prof.src.backend.database import database_handler


@dataclass
class StackRecord:
    stack_level: int
    file_path: str
    file_line: int
    caller_function_name: str


class CallStackUtils:
    @staticmethod
    def get_call_stack_map() -> Dict[int, List[StackRecord]]:
        stacks = database_handler.execute("""
            SELECT CallStacks.StackId AS StackId,
                   CallStacks.StackLevel AS StackLevel,
                   CallStacks.CallSiteLine AS Line,
                   CallFunctions.FunctionName AS FunctionName,
                   SourceFiles.SourceFilePath AS SourceFilePath
            FROM CallStacks
            LEFT JOIN CallFunctions
            ON CallFunctions.CallFunctionId=CallStacks.CallFunctionId
            LEFT JOIN SourceFiles
            ON CallFunctions.SourceFileId=SourceFiles.SourceFileId
            """).fetchall()
        stack_map = defaultdict(list)
        for st in stacks:
            stack_map[st["StackId"]].append(
                StackRecord(
                    st["StackLevel"],
                    st["SourceFilePath"],
                    st["Line"],
                    st["FunctionName"],
                )
            )
        return stack_map

    @staticmethod
    def get_instruction_call_stack(call_stack_map: Dict[int, List[StackRecord]], stack_id: int) -> List[str]:
        instr_stacks = call_stack_map[stack_id]
        instr_stacks.sort(key=lambda x: x.stack_level)
        res = []
        for st in instr_stacks:
            res.append(f"{st.file_path}:{st.file_line}\n({st.caller_function_name})\n")
        return res


class ChromeTracingUtils:
    @classmethod
    def percents_or_none(cls, value: Optional[Rational]) -> Optional[str]:
        if value is None:
            return None
        return f"{value}%"

    @classmethod
    def hex_or_none(cls, value: Optional[int]) -> Optional[str]:
        if value is None:
            return None
        return hex(value)

    @classmethod
    def round_or_none(cls, value: Optional[Rational], ndigits: int) -> Optional[float]:
        if value is None:
            return None
        return float(round(value, ndigits))
