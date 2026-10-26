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

from typing import Dict, Any, Tuple, List, Optional
from dataclasses import dataclass
import re
import logging as log
import shutil
from collections import defaultdict
import npusim.prof.src.common.utils as utils
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
import os
import json


class IncorrectStackNode(Exception):
    pass


@dataclass
class FunctionMetrics:
    self_exe_count: int = 0
    self_exe_time: int = 0
    total_exe_count: int = 0
    total_exe_time: int = 0


@dataclass
class AssemblyLine:
    addr: int
    name: str
    exec_time: int
    exec_count: int

    def __hash__(self):
        return hash(self.addr)


@dataclass
class Function:
    id: int
    file_id: Optional[int]
    name: str


@dataclass
class Backup:
    function_id: int
    functions: Dict[int, Function]
    function_metrics: Dict[int, FunctionMetrics]
    function_ids: Dict[str, int]
    graph: Dict[int, List[int]]
    instr_funcs_map: Dict[int, int]
    call_to_line: Dict[str, int]


class DatabaseManager:
    @staticmethod
    def get_assembly_lines() -> Dict[str, List[AssemblyLine]]:
        # Joins AsmMetrics with Functions table on PC range overlap
        # to get per-function assembly lines with timing data.
        msg = """
            SELECT AsmMetrics.PC AS PC,
                   AsmMetrics.InstrName AS Name,
                   COALESCE(AsmMetrics.TickCount, 0) AS Time,
                   COALESCE(AsmMetrics.ExecutionCount, 0) AS Count,
                   Functions.FunctionName AS FuncName
            FROM Functions
            INNER JOIN AsmMetrics
            ON AsmMetrics.PC
            BETWEEN Functions.SourceInstrAddr AND
            (Functions.SourceInstrAddr + Functions.FunctionSize - 1)
        """
        res = defaultdict(list)
        for i in database_handler.execute(msg).fetchall():
            res[i["FuncName"]].append(AssemblyLine(i["PC"], i["Name"], i["Time"], i["Count"]))
        return res

    @staticmethod
    def get_source_files() -> Dict[str, int]:
        msg = """
            SELECT SourceFiles.SourceFileId AS id,
                   SourceFiles.SourceFilePath AS file
            FROM SourceFiles
        """
        files = database_handler.execute(msg).fetchall()
        return {file["file"]: file["id"] for file in files}

    @staticmethod
    def insert_call_graph(stack_data: List[Tuple[int, int, Optional[int]]]) -> None:
        database_handler.executemany(
            """
            INSERT INTO CallGraph
            (CallFunctionId, ChildFunctionId, CallSiteLine)
            VALUES (?, ?, ?)
            """,
            stack_data,
        )

    @staticmethod
    def insert_call_stacks(
        stack_data: List[Tuple[int, int, int, Optional[int]]],
    ) -> None:
        database_handler.executemany(
            """
            INSERT INTO CallStacks
            (CallFunctionId, StackId, StackLevel, CallSiteLine)
            VALUES (?, ?, ?, ?)
            """,
            stack_data,
        )

    @staticmethod
    def update_instructions(instructions_data: List[Tuple[int, ...]]) -> None:
        database_handler.executemany(
            """
            INSERT INTO CallStackIDs
            (CallStackId, SourceInstrAddr)
            VALUES (?, ?)
            """,
            instructions_data,
        )

    @staticmethod
    def insert_function_metrics(
        metrics_data: List[Tuple[int, int, int, int, int]],
    ) -> None:
        database_handler.executemany(
            """
            INSERT INTO CallGraphMetrics
            (CallFunctionId,
             SelfExecutionCount, SelfTickCount,
             TotalExecutionCount, TotalTickCount)
            VALUES (?, ?, ?, ?, ?)
            """,
            metrics_data,
        )

    @staticmethod
    def insert_callable_functions(
        functions_data: List[Tuple[int, Optional[int], str]],
    ) -> None:
        database_handler.executemany(
            """
            INSERT INTO CallFunctions
            (CallFunctionId, SourceFileId, FunctionName)
            VALUES (?, ?, ?)
            """,
            functions_data,
        )

    @staticmethod
    def insert_source_files(files_data: List[Tuple[int, str]]) -> None:
        database_handler.executemany(
            """
            INSERT OR IGNORE INTO SourceFiles
            (SourceFileId, SourceFilePath)
            VALUES (?, ?)
            """,
            files_data,
        )


class CallStackAnalyzer(IAnalyzer):
    def __init__(self) -> None:
        super().__init__()
        self.kernel_name = database_handler.get_kernel_name()
        self.object_path = database_handler.get_object_path()
        self.files = DatabaseManager.get_source_files()
        self.file_id = max(self.files.values(), default=0) + 1

        self.function_id: int = 0
        self.function_name_id: int = 0

        self.functions: Dict[int, Function] = {}
        self.function_names_index: Dict[str, int] = {}
        self.function_metrics: Dict[int, FunctionMetrics] = {}
        self.function_ids: Dict[str, int] = {}

        self.calls: List[Tuple[int, int, Optional[int]]] = []
        self.stacks: List[Tuple[int, int, int, Optional[int]]] = []
        self.graph: Dict[int, List[int]] = defaultdict(list)
        self.call_to_line: Dict[str, int] = {}

        self.instr_funcs_map: Dict[int, int] = {}
        self.current_instrs: Dict[int, AssemblyLine] = {}
        self.call_parser: List[int] = []
        self.assembly_lines: Dict[str, List[AssemblyLine]] = defaultdict(list)

    @staticmethod
    def get_name() -> str:
        return "call_stack_analyzer"

    def generate_stack(self) -> Any:
        addr2line_path = shutil.which("llvm-addr2line") or shutil.which("llvm-symbolizer")
        if not addr2line_path:
            log.warning(
                "llvm-addr2line (llvm-symbolizer) was not found. Some call stack information will not be provided."
            )
            return []
        addr2line = utils.Tool(addr2line_path)
        cmd = [
            "--output-style=JSON",
            "-demangle=true",
            "-f",
            "-i",
            f"-e={self.object_path}",
        ]
        offsets = [hex(i) for i in filter(None, self.current_instrs.keys())]
        if not offsets:
            return []
        cmd += offsets

        res = addr2line.run(cmd, os.getcwd(), capture_output=True)
        res.check_returncode()
        try:
            return json.loads(res.stdout)
        except json.JSONDecodeError:
            log.warning("Failed to parse addr2line output")
            return []

    def get_unique_file_id(self, file_path: str) -> int:
        if file_path in self.files:
            return self.files[file_path]
        self.file_id += 1
        self.files[file_path] = self.file_id
        return self.file_id

    def get_function_id(self, full_id: str) -> int:
        if full_id in self.function_ids:
            return self.function_ids[full_id]
        self.function_id += 1
        self.function_ids[full_id] = self.function_id
        return self.function_id

    def get_func_name_idx(self, function_name: str) -> int:
        if function_name in self.function_names_index:
            return self.function_names_index[function_name]
        self.function_name_id += 1
        self.function_names_index[function_name] = self.function_name_id
        return self.function_name_id

    def construct_function_id(self, symbols, id, root_func: int) -> int:
        res = f"{root_func}_root_"
        for i in symbols[id:]:
            file_id = self.get_unique_file_id(i["StartFileName"])
            func_identifier = i["StartLine"]
            if not self.is_scalar_function(i["FunctionName"]):
                func_identifier = self.get_func_name_idx(i["FunctionName"])
            res += f"f_{file_id}_{func_identifier}_"
        return self.get_function_id(res)

    @staticmethod
    def is_correct_symbol(symbol: Dict[Any, Any]) -> bool:
        return len(symbol["FileName"].strip()) > 0 and len(symbol["FunctionName"].strip()) > 0

    def parse_symbols(self, stack: Dict[Any, Any], root_func: int, top_name: str) -> None:
        # Build call graph from llvm-symbolizer output:
        #   stack["Symbol"] = [innermost_frame ... outermost_frame]
        #   Walk frames, create Function nodes, add caller->callee edges.
        #   Last frame gets the kernel's top-level name.
        #   Track self-time per function at the leaf of each stack.
        pc = int(stack["Address"], 16)
        instr_exe_count = self.current_instrs[pc].exec_count
        instr_exe_time = self.current_instrs[pc].exec_time

        symbols = stack["Symbol"]
        symbols[-1]["FunctionName"] = top_name
        next_fid = None

        for id, sym in enumerate(symbols):
            name = sym["FunctionName"]
            file_id = self.get_unique_file_id(sym["StartFileName"])
            call_line = sym["Line"]
            func_id = self.construct_function_id(symbols, id, root_func)
            if not self.is_correct_symbol(sym):
                raise IncorrectStackNode
            if func_id not in self.functions:
                self.functions[func_id] = Function(func_id, file_id, name)
                self.function_metrics[func_id] = FunctionMetrics()
            if next_fid is not None and next_fid not in self.graph[func_id]:
                call_id = f"{func_id}_{next_fid}"
                self.graph[func_id].append(next_fid)
                self.call_to_line[call_id] = call_line
            if id == len(symbols) - 1 and func_id not in self.graph[root_func]:
                self.graph[root_func].append(func_id)
            next_fid = func_id

        func_id = self.construct_function_id(symbols, 0, root_func)
        self.instr_funcs_map[pc] = func_id
        self.function_metrics[func_id].self_exe_count += instr_exe_count
        self.function_metrics[func_id].self_exe_time += instr_exe_time

    def is_scalar_function(self, function: str) -> bool:
        # Scalar functions run on the scalar core (not SIMT/vector threads).
        # Identified by the absence of simt_entry, vector.thread markers.
        return "simt_entry" not in function and ".vector.thread" not in function and "_vector_thread" not in function

    def add_vf_funcs(self, root_id: int) -> Any:
        # Vector Function (VF) expansion: scan scalar assembly for VF
        # instructions that reference child functions (e.g. "VF <my_func>").
        # Symbolize those child functions and add them to the call graph.
        for func, instrs in self.assembly_lines.items():
            if not self.is_scalar_function(func):
                continue
            for instr in instrs:
                if not instr.name.startswith("VF"):
                    continue
                match = re.search(r"<(?P<name>.*)>", instr.name)
                if match is None:
                    continue
                cf_name = match.group("name")
                cf_instrs = self.assembly_lines.get(cf_name, [])
                vf_root = self.instr_funcs_map.get(instr.addr, root_id)
                smb_done = self.run_symbolizer(vf_root, cf_name, cf_instrs)

                if not smb_done:
                    self_time = sum([i.exec_time for i in instrs])
                    self_count = sum([i.exec_count for i in instrs])
                    func_id = self.get_function_id(f"vf_{instr.addr}")
                    self.functions[func_id] = Function(func_id, None, cf_name)
                    self.function_metrics[func_id] = FunctionMetrics(self_count, self_time)
                    for inner_instr in instrs:
                        self.instr_funcs_map[inner_instr.addr] = func_id
                    if func_id not in self.graph[root_id]:
                        self.graph[root_id].append(func_id)

    def progress_function_metrics(self, func_id: int) -> Tuple[int, int]:
        # Recursive DFS to propagate self_time/self_count upward:
        # total_time = self_time + sum(child.total_time for each child)
        self.function_metrics[func_id].total_exe_time += self.function_metrics[func_id].self_exe_time
        self.function_metrics[func_id].total_exe_count += self.function_metrics[func_id].self_exe_count
        for child in self.graph[func_id]:
            child_time, child_count = self.progress_function_metrics(child)
            self.function_metrics[func_id].total_exe_time += child_time
            self.function_metrics[func_id].total_exe_count += child_count
        return self.function_metrics[func_id].total_exe_time, self.function_metrics[func_id].total_exe_count

    def parse_call_stacks(self, func_id: int) -> None:
        if len(self.call_parser) != 0:
            prev_id = self.call_parser[-1]
            site_line = self.call_to_line.get(f"{prev_id}_{func_id}")
            self.calls.append((prev_id, func_id, site_line))

        self.call_parser.append(func_id)

        for depth in range(1, len(self.call_parser)):
            call = f"{self.call_parser[depth - 1]}_{self.call_parser[depth]}"
            site_line = self.call_to_line.get(call)
            self.stacks.append((self.call_parser[depth], func_id, depth, site_line))
        for next_call in self.graph[func_id]:
            self.parse_call_stacks(next_call)

        self.call_parser.pop()

    def save_collected_result(self) -> None:
        db = DatabaseManager
        db.insert_source_files([(id, path) for path, id in self.files.items()])
        db.insert_callable_functions(
            [(f.id, f.file_id, utils.clear_identifier(f.name)) for f in self.functions.values()]
        )
        db.insert_call_graph(self.calls)
        db.insert_call_stacks(self.stacks)
        db.insert_function_metrics(
            [
                (
                    fid,
                    m.self_exe_count,
                    m.self_exe_time,
                    m.total_exe_count,
                    m.total_exe_time,
                )
                for fid, m in self.function_metrics.items()
            ]
        )
        db.update_instructions([tuple(reversed(i)) for i in self.instr_funcs_map.items()])

    def set_backup(self) -> Backup:
        return Backup(
            self.function_id,
            self.functions,
            self.function_metrics,
            self.function_ids,
            self.graph,
            self.instr_funcs_map,
            self.call_to_line,
        )

    def load_backup(self, backup: Backup) -> None:
        self.function_id = backup.function_id
        self.functions = backup.functions
        self.function_metrics = backup.function_metrics
        self.function_ids = backup.function_ids
        self.graph = backup.graph
        self.instr_funcs_map = backup.instr_funcs_map
        self.call_to_line = backup.call_to_line

    def get_scalar_instrs(self) -> List[AssemblyLine]:
        res = []
        for func, instrs in self.assembly_lines.items():
            if self.is_scalar_function(func):
                res.extend(instrs)
        return res

    def get_vector_instrs(self) -> List[AssemblyLine]:
        res = []
        for func, instrs in self.assembly_lines.items():
            if not self.is_scalar_function(func):
                res.extend(instrs)
        return res

    def run_symbolizer(self, root_func: int, top_name: str, instrs: List[AssemblyLine]) -> bool:
        self.current_instrs = {i.addr: i for i in instrs}
        stacks = self.generate_stack()
        backup = self.set_backup()
        try:
            for stack in stacks:
                self.parse_symbols(stack, root_func, top_name)
        except IncorrectStackNode:
            log.warning("Incorrect info from llvm-symbolizer. Some call stack information will not be provided.")
            self.load_backup(backup)
            return False
        return True

    def make_root_func(self) -> int:
        root_name = self.kernel_name + "_root"
        root_id = self.get_function_id(root_name)
        self.functions[root_id] = Function(root_id, None, root_name)
        self.function_metrics[root_id] = FunctionMetrics()
        return root_id

    def process(self) -> None:
        root_id = self.make_root_func()
        self.assembly_lines = DatabaseManager.get_assembly_lines()
        scalar_instrs = self.get_scalar_instrs()
        self.run_symbolizer(root_id, self.kernel_name, scalar_instrs)
        self.add_vf_funcs(root_id)
        self.progress_function_metrics(root_id)
        self.parse_call_stacks(root_id)
        self.save_collected_result()

    def cleanup(self) -> None:
        self.calls.clear()
        self.stacks.clear()
        self.functions.clear()
        self.function_metrics.clear()
        self.function_ids.clear()
        self.graph.clear()
        self.instr_funcs_map.clear()
        self.call_to_line.clear()
