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

from collections import defaultdict
import os
import io
import re
import logging as log
from typing import Dict, OrderedDict, Set, Tuple, Optional, List
from dataclasses import dataclass, field

from npusim.prof.src.common.biprof_constants import NOT_AVAILABLE, UNKNOWN
from npusim.prof.src.common.biprof_enum import ArchName, InstrQueueType
from npusim.prof.src.common.utils import Tool, get_hidumper_path
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.temp_file_storage import tfs


@dataclass(frozen=True)
class Section:
    name: str


@dataclass(frozen=True)
class SourceFile:
    full_path: str


@dataclass(frozen=True)
class SourceLine:
    text: str


@dataclass(frozen=True)
class Instruction:
    section_id: int
    addr: int
    encoding: str
    text: str
    debug_info_id: Optional[int]
    qtype_value: Optional[int]


@dataclass(frozen=True)
class DebugInfo:
    file_id: Optional[int]
    line_id: Optional[int]
    line_num: int


@dataclass(frozen=True)
class BasicBlock:
    addr: int
    length: int
    name: str


@dataclass(frozen=True)
class FunctionInfo:
    addr: int
    size: int
    name: str
    is_kernel: bool


# Note that indices in that data start from 0, and
# it's required to increase them (+1)
# before inserting into the database
@dataclass(frozen=True)
class ProcessedObjData:
    sections: List[Section] = field(default_factory=list)
    source_files: List[SourceFile] = field(default_factory=list)
    source_lines: List[SourceLine] = field(default_factory=list)
    instructions: List[Instruction] = field(default_factory=list)
    debug_info: List[DebugInfo] = field(default_factory=list)
    basic_blocks: List[BasicBlock] = field(default_factory=list)


@dataclass
class FuncPosition:
    start_instr: int  # inclusive
    end_instr: int  # not inclusive
    start_bb: int  # inclusive
    end_bb: int  # not inclusive


# A simple dict
# (section_id, instr_addr) -> debug_info_id (or None)
InstructionsToDebugMapping = Dict[Tuple[int, int], Optional[int]]

cube_function_endings = [
    "_mix_aic",
]
vec_function_endings = [
    "_mix_aiv",
    "_vector_thread",
    "_simt_entry",
]


class ObjdumpPatterns:
    section_pattern = re.compile(r"^\s*Disassembly of section (?P<section_name>[a-zA-Z0-9\._]+):$")
    function_pattern = re.compile(
        r"^\s*(?:[0-9a-fA-F]){16} "
        r"<(?P<function_name>[a-zA-Z0-9_$.]+\w*)(?:\$[a-zA-Z_]+)?>:$"
    )
    user_file_pattern = re.compile(
        r"^\s*;*\s*(?P<file_path>/(?:[a-zA-Z0-9\._\-@ ]*/)*"
        r"(?!__davinci|__clang)[a-zA-Z0-9\._\- ]*):(?P<line_num>[0-9]+)$"
    )
    unknown_pattern = re.compile(
        r"^\s*(?P<addr>[a-f0-9]+):\s*(?P<encoding>"
        r"[a-f0-9]+)\s*<unknown>$"
    )
    not_available_pattern = re.compile(r"^\s*(?P<addr>[a-f0-9]+): <not available>$")
    instruction_pattern = re.compile(
        r"^\s*(?P<addr>[a-f0-9]+):\s+(?P<encoding>"
        r"(?:[a-f0-9]{2}[ ]?){4,16})\s+(?P<text>.+)$"
    )

    sym_table_func_pattern = re.compile(
        r"(?P<addr>[0-9a-fA-F]+)\s+(?P<bind>[a-z])\s+"
        r"F\s+\.text\s+(?P<size>[0-9a-fA-F]+)\s+(?P<name>[0-9A-Za-z_.$]+)"
    )

    text_section_4bytes_pattern = re.compile(r" ([0-9a-fA-F]{8})")


class HidumperPatterns:
    section_pattern = ObjdumpPatterns.section_pattern
    function_pattern = re.compile(r"^(?P<addr>[0-9a-fA-F]+)\s*(?P<function_name>[a-zA-Z0-9_$.]+):$")
    basic_block_pattern = re.compile(r"^\s*bb(?P<number>[0-9]+)@(?P<addr>[0-9a-f]+):$")
    instruction_pattern = ObjdumpPatterns.instruction_pattern
    vf_function_pattern = re.compile(r"<(?P<vf_func_name>[a-zA-Z0-9_$.]+)")


class ElfObjectReader:
    TABS_PAT = re.compile(r"\t+")
    TAGS_PAT = re.compile(r"<[^>]*>")

    def __init__(self, arch: ArchName, workspace: str) -> None:
        self.arch = arch
        self.workspace = workspace

    @classmethod
    def _clean_instruction_text(cls, text: str) -> str:
        text = cls.TABS_PAT.sub(" ", text)
        return cls.TAGS_PAT.sub("", text)

    @staticmethod
    def _clean_function_name(function_name: str) -> str:
        return re.sub(r"(\.\d+|\$local)*$", "", function_name)

    @staticmethod
    def _extract_func_name(instr_text: str) -> str:
        func_match = HidumperPatterns.vf_function_pattern.search(instr_text)
        if func_match:
            return func_match["vf_func_name"]
        return ""

    @staticmethod
    def _get_hidumper_arch(arch: ArchName) -> str:
        if arch == ArchName.V100:
            return ArchName.V100.value
        raise RuntimeError("Unsupported architecture to use hidumper.")

    @classmethod
    def _read_objdump_lines(
        cls, objdump_lines: List[str]
    ) -> Tuple[
        List[Section],
        List[SourceFile],
        List[SourceLine],
        List[Instruction],
        List[DebugInfo],
        InstructionsToDebugMapping,
    ]:
        sections: List[Section] = []
        source_files_map: OrderedDict[SourceFile, int] = OrderedDict()
        source_lines_map: OrderedDict[SourceLine, int] = OrderedDict()
        instructions: List[Instruction] = []
        debug_info: List[DebugInfo] = []
        instrs_to_debug_mapping: InstructionsToDebugMapping = {}

        cur_qtype_value: Optional[int] = None

        for i, line in enumerate(objdump_lines):
            # Section name
            match = ObjdumpPatterns.section_pattern.fullmatch(line)
            if match:
                sections.append(Section(match["section_name"]))
                continue
            # Function name (global (kernel) or some other function's name)
            match = ObjdumpPatterns.function_pattern.fullmatch(line)
            if match:
                function_name = match["function_name"]
                cur_qtype_value = cls.get_function_type_by_name(function_name)
            # Source file path & line number
            file_id, line_id = None, None
            match = ObjdumpPatterns.user_file_pattern.fullmatch(line)
            if match:
                file_path = os.path.realpath(match["file_path"])
                line_num = int(match["line_num"])
                file_id = source_files_map.get(SourceFile(file_path), None)
                if file_id is None:
                    file_id = len(source_files_map)
                    source_files_map[SourceFile(file_path)] = file_id
                if os.path.isfile(file_path):
                    source_line = objdump_lines[i + 1].strip()
                    if source_line.startswith(";"):
                        source_line = source_line[1:].strip()
                    line_id = source_lines_map.get(SourceLine(source_line), None)
                    if line_id is None:
                        line_id = len(source_lines_map)
                        source_lines_map[SourceLine(source_line)] = line_id
                debug_info.append(DebugInfo(file_id, line_id, line_num))
                continue
            # Unknown instruction pattern
            # (some strange instruction for objdump)
            match = ObjdumpPatterns.unknown_pattern.fullmatch(line)
            if match:
                assert sections, "Unknown assembly instruction belongs to unknown section!"
                instr_addr = int(match["addr"], base=16)
                instr_encoding = match["encoding"]
                instr_text = UNKNOWN
                section_id = len(sections) - 1
                debug_info_id = len(debug_info) - 1 if len(debug_info) > 0 else None
                instr = Instruction(
                    section_id,
                    instr_addr,
                    instr_encoding,
                    instr_text,
                    debug_info_id,
                    cur_qtype_value,
                )
                instructions.append(instr)
                instrs_to_debug_mapping[(section_id, instr_addr)] = debug_info_id
                continue
            # Not available instruction pattern
            # (some known but hidden instruction)
            match = ObjdumpPatterns.not_available_pattern.fullmatch(line)
            if match:
                assert sections, "Not available assembly instruction belongs to unknown section!"
                instr_addr = int(match["addr"], base=16)
                instr_encoding = NOT_AVAILABLE
                instr_text = NOT_AVAILABLE
                section_id = len(sections) - 1
                debug_info_id = len(debug_info) - 1 if len(debug_info) > 0 else None
                instr = Instruction(
                    section_id,
                    instr_addr,
                    instr_encoding,
                    instr_text,
                    debug_info_id,
                    cur_qtype_value,
                )
                instructions.append(instr)
                instrs_to_debug_mapping[(section_id, instr_addr)] = debug_info_id
                continue
            # Source instruction
            match = ObjdumpPatterns.instruction_pattern.fullmatch(line)
            if match:
                assert sections, "Assembly instruction belongs to unknown section!"
                instr_addr = int(match["addr"], base=16)
                instr_encoding = match["encoding"]
                instr_text = cls._clean_instruction_text(match["text"])
                section_id = len(sections) - 1
                debug_info_id = len(debug_info) - 1 if len(debug_info) > 0 else None
                instr = Instruction(
                    section_id,
                    instr_addr,
                    instr_encoding,
                    instr_text,
                    debug_info_id,
                    cur_qtype_value,
                )
                instructions.append(instr)
                instrs_to_debug_mapping[(section_id, instr_addr)] = debug_info_id
                continue
        _read_objdump_lines = (
            sections,
            list(source_files_map.keys()),
            list(source_lines_map.keys()),
            instructions,
            debug_info,
            instrs_to_debug_mapping,
        )
        return _read_objdump_lines

    @classmethod
    def _read_hidumper_lines(
        cls,
        hidumper_lines: List[str],
        instrs_to_debug_mapping: InstructionsToDebugMapping,
        kernel_name: str,
    ) -> Tuple[List[Instruction], List[BasicBlock]]:
        sections: List[Section] = []
        instructions: List[Instruction] = []
        basic_blocks: List[BasicBlock] = []
        cur_qtype_value: Optional[int] = None
        current_func: str = ""
        func_positions: Dict[str, FuncPosition] = {}
        used_funcs: Set[str] = set()
        kernel_names = [kernel_name, kernel_name + "_mix_aic", kernel_name + "_mix_aiv"]
        prev_bb_start_pos = 0
        for line in hidumper_lines:
            # Section name
            match = HidumperPatterns.section_pattern.fullmatch(line)
            if match:
                sections.append(Section(match["section_name"]))
                continue
            # Function name (global (kernel) or some other function's name)
            match = HidumperPatterns.function_pattern.fullmatch(line)
            if match:
                function_name = match["function_name"]
                if function_name in kernel_names:
                    used_funcs.add(function_name)
                cur_qtype_value = cls.get_function_type_by_name(function_name)
                if current_func != function_name:
                    if current_func:
                        func_positions[current_func].end_instr = len(instructions)
                        func_positions[current_func].end_bb = len(basic_blocks)
                    current_func = function_name
                    func_positions[current_func] = FuncPosition(len(instructions), -1, len(basic_blocks), -1)
                continue
            # Basic block number
            match = HidumperPatterns.basic_block_pattern.fullmatch(line)
            if match:
                assert sections, "Basic block belongs to unknown section!"
                if instructions:
                    basic_blocks[-1] = BasicBlock(
                        basic_blocks[-1].addr,
                        len(instructions) - prev_bb_start_pos,
                        basic_blocks[-1].name,
                    )
                    prev_bb_start_pos = len(instructions)
                basic_blocks.append(BasicBlock(int(match["addr"], base=16), 0, f"Basic Block {match['number']}"))
                continue
            # Source instruction
            match = HidumperPatterns.instruction_pattern.fullmatch(line)
            if match:
                assert sections, "Assembly instruction belongs to unknown section!"
                instr_addr = int(match["addr"], base=16)
                instr_encoding = match["encoding"]
                instr_text = cls._clean_instruction_text(match["text"])
                if instr_text.startswith("VF") and current_func in kernel_names:
                    func_name = cls._extract_func_name(match["text"])
                    instr_text += f" <{func_name}>"
                    used_funcs.add(func_name)
                section_id = len(sections) - 1
                debug_info_id = instrs_to_debug_mapping.get((section_id, instr_addr), None)
                instr = Instruction(
                    section_id,
                    instr_addr,
                    instr_encoding,
                    instr_text,
                    debug_info_id,
                    cur_qtype_value,
                )
                instructions.append(instr)
                instrs_to_debug_mapping[(section_id, instr_addr)] = debug_info_id
                continue
        assert current_func, "No functions detected. Input file may be incomplete or corrupted."
        if func_positions[current_func].end_instr == -1:
            func_positions[current_func].end_instr = len(instructions)
            func_positions[current_func].end_bb = len(basic_blocks)
        basic_blocks[-1] = BasicBlock(
            basic_blocks[-1].addr,
            len(instructions) - prev_bb_start_pos,
            basic_blocks[-1].name,
        )
        res_instructions = []
        res_basic_blocks = []
        for func in used_funcs:
            assert func in func_positions, f"Function {func} instructions not found"
            res_instructions += instructions[func_positions[func].start_instr : func_positions[func].end_instr]
            res_basic_blocks += basic_blocks[func_positions[func].start_bb : func_positions[func].end_bb]
        return res_instructions, res_basic_blocks

    @classmethod
    def _process_obj_default(cls, objdump_lines: List[str]) -> ProcessedObjData:
        sections, source_files, source_lines, instructions, debug_info, _ = cls._read_objdump_lines(objdump_lines)

        return ProcessedObjData(
            sections=sections,
            source_files=source_files,
            source_lines=source_lines,
            instructions=instructions,
            debug_info=debug_info,
            basic_blocks=[],
        )

    @classmethod
    def _process_obj_combined(
        cls, objdump_lines: List[str], hidumper_lines: List[str], kernel_name: str
    ) -> ProcessedObjData:
        sections, source_files, source_lines, _, debug_info, instrs_to_debug_mapping = cls._read_objdump_lines(
            objdump_lines
        )
        instructions, basic_blocks = cls._read_hidumper_lines(hidumper_lines, instrs_to_debug_mapping, kernel_name)

        return ProcessedObjData(
            sections=sections,
            source_files=source_files,
            source_lines=source_lines,
            instructions=instructions,
            debug_info=debug_info,
            basic_blocks=basic_blocks,
        )

    # Uses hidumper the first time; returns cached hidumper output then
    def _use_hidumper_cached(self, hidumper_path: str, obj_path: str) -> List[str]:
        lines: List[str] = []

        with tfs.open_auto("hidumper_output", key=obj_path) as f:
            # No cached output: write
            if "w" in f.mode:
                hidumper = Tool(hidumper_path)
                cmd = [
                    "-d",
                    "full",
                    "-a",
                    self._get_hidumper_arch(self.arch),
                    "-i",
                    obj_path,
                ]
                if biprof_ctx.kernel_name:
                    cmd.extend(["--kernel-name", str(biprof_ctx.kernel_name)])
                if biprof_ctx.force_disassembly:
                    cmd.append("--no-verify")
                res = hidumper.run(cmd, self.workspace, capture_output=True)
                res.check_returncode()
                lines = res.stdout.splitlines()
                f.write(res.stdout)
                log.debug(f"hidumper output cached as {f.name}")
            # Output was cached: just read it
            else:
                lines = f.read().split("\n")
                log.debug("hidumper output was retrieved from cache file.")
        return lines

    @staticmethod
    def _should_read_function(is_global: bool, is_other: bool, read_kernels: bool, read_other: bool) -> bool:
        return (is_global and read_kernels) or (is_other and read_other)

    # Uses llvm-objdump
    def read_functions_info(
        self, obj_path: str, read_kernels: bool = True, read_other: bool = True
    ) -> List[FunctionInfo]:
        functions_info = []

        objdump = Tool("llvm-objdump")
        cmd = ["-t", obj_path]
        ret = objdump.run(cmd, self.workspace, capture_output=True)
        ret.check_returncode()

        for line in io.StringIO(ret.stdout):
            match = ObjdumpPatterns.sym_table_func_pattern.match(line)
            if match is not None:
                addr = int(match["addr"], 16)
                bind = match["bind"]
                name = match["name"]
                size = int(match["size"], 16)
                is_global = bind == "g"  # kernels are "global"
                is_other = bind in ["l", "w"]  # other functions are not
                if self._should_read_function(is_global, is_other, read_kernels, read_other):
                    function_info = FunctionInfo(
                        addr=addr,
                        size=size,
                        name=name,
                        is_kernel=is_global,
                    )
                    functions_info.append(function_info)
        return functions_info

    # Uses hidumper
    # Returns an empty array if hidumper is not available
    def read_hidumper_detected_functions_info(self, obj_path: str) -> List[FunctionInfo]:
        functions: List[FunctionInfo] = []

        hidumper_path = get_hidumper_path()
        if not hidumper_path:
            return functions
        lines = self._use_hidumper_cached(hidumper_path, obj_path)

        last_instr_addr = 0
        last_instr_size = 0
        for line in lines:
            match = HidumperPatterns.instruction_pattern.fullmatch(line)
            if match:
                last_instr_addr = int(match["addr"], 16)
                last_instr_size = int(len(match["encoding"]) / 2)
            match = HidumperPatterns.function_pattern.fullmatch(line)
            if match:
                name = match["function_name"]
                addr = int(match["addr"], 16)
                if functions:
                    functions[-1] = FunctionInfo(
                        functions[-1].addr,
                        last_instr_addr + last_instr_size - functions[-1].addr,
                        functions[-1].name,
                        False,
                    )
                functions.append(FunctionInfo(addr, -1, name, False))
        functions[-1] = FunctionInfo(
            functions[-1].addr,
            last_instr_addr + last_instr_size - functions[-1].addr,
            functions[-1].name,
            False,
        )
        return [func for func in functions if "hidumper_autodetected" in func.name]

    # Recognizes function type by its name.
    # Returns:
    # InstrQueueType.CUBE: if this function contains CUBE instructions;
    # InstrQueueType.SIMD: if this function contains VEC instructions;
    # None: if it's not easy to recognize the type of the function by its name.
    @classmethod
    def get_function_type_by_name(cls, function_name: str) -> Optional[int]:
        clean_function_name = cls._clean_function_name(function_name)

        if cls._ends_with_any(clean_function_name, cube_function_endings):
            return InstrQueueType.CUBE.value
        elif cls._ends_with_any(clean_function_name, vec_function_endings):
            return InstrQueueType.SIMD.value
        else:
            return None

    @staticmethod
    def _ends_with_any(name: str, endings: List[str]) -> bool:
        return any(name.endswith(ending) for ending in endings)

    # Returns groups of kernel function names
    # (see unit tests for example)
    @staticmethod
    def get_groups_from_function_names(function_names: Set[str]) -> Dict[str, Set[str]]:
        grouped_names: Dict[str, Set[str]] = defaultdict(set)
        for f_name in function_names:
            clean_name = f_name
            if f_name.endswith("_mix_aic") or f_name.endswith("_mix_aiv"):
                clean_name_tmp = f_name[: -len("_mix_ai*")]
                cond1 = f_name.endswith("_mix_aic") and f"{clean_name_tmp}_mix_aiv" in function_names
                cond2 = f_name.endswith("_mix_aiv") and f"{clean_name_tmp}_mix_aic" in function_names
                if cond1 or cond2:
                    clean_name = clean_name_tmp
                    grouped_names[clean_name].add(f_name)
                else:
                    grouped_names[clean_name].add(clean_name)
            else:
                grouped_names[clean_name].add(clean_name)
        return grouped_names

    # Uses llvm-objdump
    # (but bytes order differs from read_as_text results)
    def read_text_section_bytes(self, obj_path: str) -> bytearray:
        objdump = Tool("llvm-objdump")
        cmd = ["-s", "--section", ".text", obj_path]
        ret = objdump.run(cmd, self.workspace, capture_output=True)
        ret.check_returncode()

        bytes = bytearray()

        for line in io.StringIO(ret.stdout):
            bytes_4 = ObjdumpPatterns.text_section_4bytes_pattern.findall(line)
            for item in bytes_4:
                bytes += bytearray.fromhex(str(item))

        return bytes

    # Uses llvm-objdump by default (if hidumper_path is not specified)
    def read_as_text(self, obj_path: str, hidumper_path: Optional[str] = None) -> List[str]:
        if hidumper_path:
            lines = self._use_hidumper_cached(hidumper_path, obj_path)
        else:
            objdump = Tool("llvm-objdump")
            cmd = ["-d", "--source", "--line-numbers"]
            if biprof_ctx.kernel_name:
                kernel_name = str(biprof_ctx.kernel_name)
                cmd.append(f"--disassemble-symbols={kernel_name}")
                cmd.append(f"--disassemble-symbols={kernel_name}_mix_aic")
                cmd.append(f"--disassemble-symbols={kernel_name}_mix_aiv")
            cmd.append(obj_path)
            res = objdump.run(cmd, self.workspace, capture_output=True)
            res.check_returncode()
            lines = res.stdout.splitlines()
        return lines

    def read(self, obj_path: str, kernel_name: str) -> ProcessedObjData:
        hidumper_path = get_hidumper_path()
        hidumper_lines, objdump_lines = None, None
        # Case 1: hidumper is available
        if hidumper_path:
            with StageReporter("Disassemble the kernel"):
                hidumper_lines = self.read_as_text(obj_path, hidumper_path)
                assert hidumper_lines, "Disassembly info is not found"
            with StageReporter("Retrieve source line mapping"):
                objdump_lines = self.read_as_text(obj_path, hidumper_path=None)
                if not objdump_lines:
                    log.warning("Source lines info is not found")
            with StageReporter("Process assembly lines"):
                processed_obj_info = self._process_obj_combined(objdump_lines, hidumper_lines, kernel_name)
        # Case 2: only llvm-objdump is available
        else:
            with StageReporter("Disassemble the kernel & retrieve source line mapping"):
                objdump_lines = self.read_as_text(obj_path, hidumper_path=None)
                assert objdump_lines, "Disassembly info is not found"
            with StageReporter("Process assembly lines"):
                processed_obj_info = self._process_obj_default(objdump_lines)
        return processed_obj_info
