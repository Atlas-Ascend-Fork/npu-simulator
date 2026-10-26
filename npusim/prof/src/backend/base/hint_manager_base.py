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
import traceback
import logging as log
import os
from collections import defaultdict
from typing import Dict, List, Optional, OrderedDict, Type
from npusim.prof.src.backend.hints.base.hint_base import HintDataItem, IHint
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.utils import (
    Timer,
    is_in_pyinstaller_env,
    get_pyinstaller_path,
    read_file_line,
)
from npusim.prof.src.common.stage_reporter import StageReporter


class HintManagerBase:
    def __init__(self) -> None:
        self.hints_order: List[IHint] = []
        self.supported_hints: List[Type[IHint]] = []
        self.result: List[HintDataItem] = []

    def add_hint(self, hint: IHint) -> None:
        self.hints_order.append(hint)

    # Returns mapping from LineId to asm metrics instrs of this line.
    # In case when DebugInfo is not available,
    # the result will contain only 1 key (None) mapped to all instrs
    @staticmethod
    def get_instrs_map() -> Dict[Optional[int], List[sqlite3.Row]]:
        query = database_handler.execute("""
        SELECT * FROM AsmMetrics
        WHERE ExecutionCount != 0
        """)
        rows = query.fetchall()
        result: Dict[Optional[int], List[sqlite3.Row]] = defaultdict(list)
        for instr in rows:
            result[instr["LineId"]].append(instr)
        return result

    @staticmethod
    def save_hint_type_state(hint_name: str, hint_passed: bool) -> None:
        database_handler.execute(
            """
        UPDATE HintTypes
        SET HintPassed = :hint_passed
        WHERE HintTypeName = :hint_name
        """,
            {"hint_name": hint_name, "hint_passed": hint_passed},
        )

    @staticmethod
    def save_hint_msg(hint_text: str) -> Optional[int]:
        database_handler.execute(
            """
        INSERT INTO HintMessages (HintMsgText) VALUES (?)
        """,
            (hint_text,),
        )
        return database_handler.get_lastrowid()

    def save_hints(self) -> None:
        for h_item in self.result:
            for h_target in h_item.targets:
                hint_msg_id = self.save_hint_msg(h_item.hint_text)
                if h_target.is_kernel:
                    database_handler.execute(
                        """
                    INSERT INTO KernelHints
                    (HintTypeId, HintMsgId)
                    VALUES
                    (?, ?)
                    """,
                        (h_item.hint_type_id, hint_msg_id),
                    )
                elif h_target.pc:
                    database_handler.execute(
                        """
                    INSERT INTO InstructionHints
                    (HintTypeId, HintMsgId, PC)
                    VALUES
                    (?, ?, ?)
                    """,
                        (h_item.hint_type_id, hint_msg_id, h_target.pc),
                    )
                elif h_target.sourceline_id:
                    database_handler.execute(
                        """
                    INSERT INTO SourceLineHints
                    (HintTypeId, HintMsgId, SourceLineId)
                    VALUES
                    (?, ?, ?)
                    """,
                        (h_item.hint_type_id, hint_msg_id, h_target.sourceline_id),
                    )
        database_handler.commit()

    @staticmethod
    def get_callstack(stack_id: int) -> List[sqlite3.Row]:
        msg = """
        SELECT CallStacks.CallSiteLine AS line,
               CallFunctions.FunctionName AS name,
               SourceFiles.SourceFilePath AS file_path
        FROM CallStacks
        LEFT JOIN CallFunctions
        ON        CallFunctions.CallFunctionId=CallStacks.CallFunctionId
        LEFT JOIN SourceFiles
        ON        SourceFiles.SourceFileId=CallFunctions.SourceFileId
        WHERE CallStacks.StackId=:stack_id
        ORDER BY CallStacks.StackLevel ASC
        """
        return database_handler.execute(msg, {"stack_id": stack_id}).fetchall()

    @staticmethod
    def _has_sourceline_info(sourceline: Optional[sqlite3.Row]) -> bool:
        return sourceline is not None and sourceline["FilePath"] and sourceline["LineNum"] and sourceline["LineText"]

    @classmethod
    def print_hint_msg(
        cls,
        hint_text: str,
        callstack_id: Optional[int],
        sourceline: Optional[sqlite3.Row],
    ) -> None:
        msg = ""
        msg += "\n---------------INEFFICIENCY DETECTED---------------\n"
        # Callstacks and source files & lines are available
        if callstack_id and sourceline:
            for node in cls.get_callstack(callstack_id):
                msg += f"{node['file_path']}:{node['line']}\n"
                msg += f"{node['name']}\n"
            line_text = read_file_line(sourceline["FilePath"], sourceline["LineNum"])
            msg += f"{line_text}\n" if line_text else "\n"
        # Callstacks are not available but source file & line are available
        elif cls._has_sourceline_info(sourceline):
            msg += (
                f"{sourceline['FilePath']}"
                f":{sourceline['LineNum']}\n"
                f"line {sourceline['LineNum']}:\n"
                f"{sourceline['LineText']}\n"
            )
        msg += f"{hint_text}\n"
        msg += "---------------------------------------------------"
        log.debug(msg)

    # Returns mapping from LineId to source file and source line info
    def get_sourcelines(self) -> Dict[Optional[int], sqlite3.Row]:
        query = database_handler.execute("""
        SELECT
            SourceLines.SourceLineId AS LineId,
            SourceFiles.SourceFilePath AS FilePath,
            DebugInfo.DebugInfoLineNum AS LineNum,
            SourceLines.SourceLine AS LineText
        FROM SourceLines
        LEFT JOIN DebugInfo
        ON        SourceLines.SourceLineId = DebugInfo.SourceLineId
        LEFT JOIN SourceFiles
        ON        SourceFiles.SourceFileId = DebugInfo.SourceFileId
        GROUP BY LineId
        """)
        rows = query.fetchall()
        sourcelines: Dict[Optional[int], sqlite3.Row] = dict()
        for row in rows:
            line_id = int(row["LineId"])
            sourcelines[line_id] = row
        return sourcelines

    def run_hints(self) -> None:
        with StageReporter("Execution of built-in hints"):
            instrs_map = self.get_instrs_map()
            sourcelines = self.get_sourcelines()
            for hint in self.hints_order:
                name = hint.get_name()
                status = True
                with StageReporter(f"Executing built-in hint {name}"):
                    try:
                        timer = Timer()
                        h_result = hint.process(instrs_map)
                        for h_item in h_result:
                            for h_target in h_item.targets:
                                self.print_hint_msg(
                                    h_item.hint_text,
                                    h_target.callstack_id,
                                    sourcelines.get(h_target.sourceline_id, None),
                                )
                        self.result.extend(h_result)
                    except Exception as err:
                        if biprof_ctx.strict:
                            raise err
                        status = False
                        log.warning(f"{name} hint failed: {err}")
                        log.debug(traceback.format_exc())
                    else:
                        log.debug(f"{name} hint completed successfully ({timer.count():.3f} s)")
                    finally:
                        self.save_hint_type_state(name, status)
                        database_handler.commit()
            self.save_hints()

    def register_hints(self, hints_list="all") -> int:
        base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "..")
        if is_in_pyinstaller_env():
            base_path = get_pyinstaller_path()
        with open(os.path.join(base_path, "sql/hints.sql"), "r") as file:
            database_handler.execute_script(file.read())
        hints_map = OrderedDict((hint.get_name(), hint) for hint in self.supported_hints)
        hint_names: List[str] = []
        if hints_list == "all":
            hint_names = [hint.get_name() for hint in self.supported_hints]
        else:
            hint_names = hints_list.split(",")
        disabled_hints = biprof_ctx.disable_hints.split(",")
        for name in hint_names:
            if name not in hints_map:
                log.error(f"Invalid hint name - {name}")
                return 1
            if name in disabled_hints:
                log.debug(f"Skip disabled '{name}' hint")
                continue
            try:
                hint = hints_map[name]()
            except Exception as err:
                if biprof_ctx.strict:
                    raise err
                log.warning(f"{name} hint failed to initialize: {err}")
                log.debug(traceback.format_exc())
                continue
            self.add_hint(hint)
        database_handler.executemany(
            """
        INSERT INTO HintTypes (HintTypeName)
        VALUES(?)
        """,
            [(hint.get_name(),) for hint in self.hints_order],
        )
        return 0
