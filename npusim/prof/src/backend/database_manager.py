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

from typing import List
from dataclasses import astuple

import npusim.prof.src.common.utils as utils
from npusim.prof.src.common.biprof_constants import unit_map
from npusim.prof.src.common.biprof_enum import (
    CoreType,
    InstrQueueType,
    InstrType,
)
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.elf_object_reader import (
    Section,
    SourceFile,
    SourceLine,
    Instruction,
    DebugInfo,
    BasicBlock,
    ProcessedObjData,
    FunctionInfo,
)


class BackendDBManager:
    @staticmethod
    def _save_sections(sections: List[Section]) -> None:
        database_handler.executemany(
            """
        INSERT INTO Sections (SectionName) VALUES (?)
        """,
            [(section.name,) for section in sections],
        )

    @staticmethod
    def _save_source_files(source_files: List[SourceFile]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SourceFiles (SourceFilePath) VALUES (?)
        """,
            [(file.full_path,) for file in source_files],
        )

    @staticmethod
    def _save_source_lines(source_lines: List[SourceLine]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SourceLines (SourceLine) VALUES (?)
        """,
            [(line.text,) for line in source_lines],
        )

    @staticmethod
    def _save_debug_info(debug_info_list: List[DebugInfo]) -> None:
        # Indices correction (+1) is required due to the difference between
        # Python (starting from 0) and SQLite (starting from 1) indices
        database_handler.executemany(
            """
        INSERT INTO DebugInfo
        (SourceFileId, SourceLineId, DebugInfoLineNum)
        VALUES
        (?, ?, ?)
        """,
            [
                (
                    debug_info.file_id + 1 if debug_info.file_id is not None else None,
                    debug_info.line_id + 1 if debug_info.line_id is not None else None,
                    debug_info.line_num,
                )
                for debug_info in debug_info_list
            ],
        )

    @staticmethod
    def _save_instructions(instructions: List[Instruction]) -> None:
        database_handler.executemany(
            """
        INSERT INTO SourceInstructions
        (SourceInstrSection,
         SourceInstrAddr,
         SourceInstrEncoding,
         SourceInstrText,
         SourceInstrQueueType,
         DebugInfoId)
        VALUES
        (?, ?, ?, ?, ?, ?)
        """,
            [
                (
                    instr.section_id + 1,
                    instr.addr,
                    instr.encoding,
                    instr.text,
                    instr.qtype_value,
                    instr.debug_info_id + 1 if instr.debug_info_id is not None else None,
                )
                for instr in instructions
            ],
        )

    @staticmethod
    def _save_basic_blocks(basic_blocks: List[BasicBlock]) -> None:
        database_handler.executemany(
            """
        INSERT INTO BasicBlocks
        (SourceInstrAddr, BBLength, BBName)
        VALUES
        (?, ?, ?)
        """,
            [astuple(basic_block) for basic_block in basic_blocks],
        )

    @staticmethod
    def _save_functions(functions: List[FunctionInfo]) -> None:
        database_handler.executemany(
            """
        INSERT INTO Functions
        (SourceInstrAddr, FunctionSize, FunctionName)
        VALUES
        (?, ?, ?)
        """,
            [(func.addr, func.size, func.name) for func in functions],
        )

    @classmethod
    def save_processed_data(cls, data: ProcessedObjData, functions: List[FunctionInfo]) -> None:
        cls._save_sections(data.sections)
        cls._save_source_files(data.source_files)
        cls._save_source_lines(data.source_lines)
        cls._save_instructions(data.instructions)
        cls._save_debug_info(data.debug_info)
        cls._save_basic_blocks(data.basic_blocks)
        cls._save_functions(functions)
        database_handler.commit()

    @staticmethod
    def fill_static_tables() -> None:
        database_handler.executemany(
            """
        INSERT OR IGNORE INTO InstrTypes (InstrTypeId, InstrTypeName)
        VALUES(?, ?)
        """,
            [(type.value, type.name) for type in list(InstrType)],
        )
        database_handler.executemany(
            """
        INSERT OR IGNORE INTO CoreTypes (CoreTypeId, CoreTypeName)
        VALUES(?, ?)
        """,
            [(type.value, type.name) for type in unit_map[database_handler.get_arch()]],
        )
        database_handler.executemany(
            """
        INSERT OR IGNORE INTO InstrQueueTypes (InstrQueueTypeId, InstrQueueTypeName)
        VALUES(?, ?)
        """,
            [(type.value, type.name) for type in list(InstrQueueType)],
        )
        database_handler.commit()

    @staticmethod
    def get_active_cores() -> List[int]:
        core_ids = database_handler.execute("""
        SELECT DISTINCT(ExecInstrCoreId)
        FROM ExecutedInstructions
        """).fetchall()
        available_cores = [el["execInstrCoreId"] for el in core_ids]
        return utils.parse_core_id_spec(biprof_ctx.core_id, available_cores)

    @staticmethod
    def get_active_core_types() -> List[int]:
        core_ids = database_handler.execute("""
        SELECT DISTINCT(CoreTypeId)
        FROM ExecutedInstructions
        """).fetchall()
        return [el["CoreTypeId"] for el in core_ids]

    @classmethod
    def get_active_core_types_ordered(cls) -> List[int]:
        core_type_ids = cls.get_active_core_types()
        return sorted(core_type_ids, key=lambda id: CoreType(id).name)
