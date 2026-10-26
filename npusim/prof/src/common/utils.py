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

import hashlib
import logging as log
import os
import shutil
import subprocess
import re
from typing import Optional, List, Dict, Any, Set
import datetime
import shlex
import sys
import time
from npusim.prof.src.common.biprof_constants import TICK_TO_US_COEFF, frequency_ghz_map

from npusim.prof.src.common.biprof_context import biprof_ctx


def run_cmd(
    cmd: List[str],
    workspace: str = ".",
    child_env: Optional[Dict[str, str]] = None,
    capture_output: bool = False,
    stdout: Any = None,
    check_return: bool = True,
    suppress_logs: bool = False,
) -> subprocess.CompletedProcess:
    if not suppress_logs:
        log.debug(
            'Execute command:\n %s\n in directory "%s".',
            # novm: shlex.join is 3.8+, guarded by the version check below
            shlex.join(cmd) if sys.version_info[:3] >= (3, 8) else str(cmd),
            workspace,
        )

    if not child_env:
        child_env = os.environ.copy()
    elif not suppress_logs:
        log.debug("Modified environment is %s", child_env)

    res = subprocess.run(
        cmd,
        cwd=workspace,
        capture_output=capture_output,
        text=True,
        encoding="ISO-8859-1",
        env=child_env,
        stdout=stdout,
    )
    if check_return and res.returncode != 0:
        log.critical("Command %s execution has failed.", " ".join(cmd))
        log.critical("STDERR:\n %s", res.stderr)
        return res

    if not suppress_logs and res.stderr:
        log.debug("STDERR:\n %s", res.stderr)
    return res


def get_files_by_suffix(dir: str, suffix: str) -> List[str]:
    res = [file for file in os.listdir(dir) if file.endswith(suffix)]
    return res


def clear_identifier(demangled_identifier: str) -> str:
    # Strip template arguments (<...>) and function parameters ((...))
    # from a demangled C++ identifier by tracking nesting depth.
    # E.g. "foo<int>(bar)" -> "foo"
    cleared = ""
    curved_braces_counter = 0
    corner_braces_counter = 0
    for ch in demangled_identifier:
        if ch == "<":
            corner_braces_counter += 1
        elif ch == ">":
            corner_braces_counter -= 1
        elif ch == "(":
            curved_braces_counter += 1
        elif ch == ")":
            curved_braces_counter -= 1
        elif corner_braces_counter == 0 and curved_braces_counter == 0:
            cleared += ch
    return cleared


def get_data_folders(kernel_name: str, dir: str) -> List[str]:
    folders_list = [folder for folder in os.listdir(dir) if os.path.isdir(os.path.join(dir, folder))]
    regex = re.compile(rf"{kernel_name}_[0-9]+")
    res_fold_list = [folder for folder in folders_list if regex.fullmatch(folder) is not None]
    return res_fold_list


def is_in_pyinstaller_env():
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_pyinstaller_path():
    assert is_in_pyinstaller_env(), "PyInstaller path is not available!"
    return getattr(sys, "_MEIPASS")


def get_hidumper_path() -> Optional[str]:
    path = shutil.which("hidumper")
    if path is None and is_in_pyinstaller_env():
        biprof_dir = os.path.dirname(sys.executable)
        hidumper_path = os.path.join(biprof_dir, "hidumper")
        if os.path.isfile(hidumper_path):
            path = hidumper_path
    return path


class Tool:
    def __init__(self, name: str) -> None:
        tool_path = shutil.which(name)
        if not tool_path:
            log.error(f"Tool '{name}' is not found")
            raise ValueError
        if not os.path.exists(tool_path):
            log.error(f"Couldn't find '{name}' executable.")
            raise ValueError
        self._exe_path = tool_path

    def run(
        self,
        args: List[str],
        work_dir: str = ".",
        child_env: Optional[Dict[str, str]] = None,
        capture_output: bool = False,
        stdout: Any = None,
        check_return: bool = True,
        suppress_logs: bool = False,
    ) -> subprocess.CompletedProcess:
        cmd = [self._exe_path]
        cmd.extend(args)
        return run_cmd(
            cmd=cmd,
            workspace=work_dir,
            child_env=child_env,
            capture_output=capture_output,
            stdout=stdout,
            check_return=check_return,
            suppress_logs=suppress_logs,
        )


class Timer:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._timer = time.perf_counter()

    def count(self) -> float:
        current = time.perf_counter()
        return current - self._timer


class Message:
    @staticmethod
    def status_message(message: str) -> datetime.datetime:
        tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
        time_now = datetime.datetime.now(tz)
        cur_time = time_now.strftime("%H:%M:%S.%f")[:-3]
        log.debug(f"[{cur_time}]{message}")
        return time_now


def extract_k_bits(src_num: int, start_bit: int, k: int = 1) -> int:
    # Extract k consecutive bits from src_num starting at start_bit.
    # E.g. extract_k_bits(0b11010110, start_bit=2, k=3) = 0b101 = 5
    return (src_num >> start_bit) & ((1 << k) - 1)


# Composite instruction ID is used to guarantee instruction uniqueness across
# cores/sub-cores and avoid "UNIQUE constraint" failure on inserting
# instruction into DB
#
# 64-bit composite instruction ID structure:
#    63              40 39 38       32 31             0
#    +----------------+--+-----------+----------------+
#    |   reserved     |CT|   coreID  |  original ID   |
#    |   (24 bits)    |2b|   (6b)    |    (32 bits)   |
#    +----------------+--+-----------+----------------+
#
# Where:
#     - original ID: bits 31..0
#     - coreID:      bits 37..32 (6 bits, shift = 32)
#     - coreTypeID:  bits 39..38 (2 bits, shift = 38)
#     - reserved:    bits 63..40

INSTR_ID_BITS = 32
CORE_ID_BITS = 6
CORE_TYPE_ID_BITS = 2

INSTR_ID_MASK = (1 << INSTR_ID_BITS) - 1
CORE_ID_MASK = (1 << CORE_ID_BITS) - 1
CORE_TYPE_ID_MASK = (1 << CORE_TYPE_ID_BITS) - 1

CORE_ID_SHIFT = INSTR_ID_BITS
CORE_TYPE_ID_SHIFT = INSTR_ID_BITS + CORE_ID_BITS


def compose_instr_id(instr_id: int, core_id: int, core_type_id: int) -> int:
    # Pack three fields into a 64-bit composite ID for DB uniqueness:
    #   bits [31:0]  = original instruction ID
    #   bits [37:32] = core ID (max 63)
    #   bits [39:38] = core type ID (max 3)
    return (
        ((core_type_id & CORE_TYPE_ID_MASK) << CORE_TYPE_ID_SHIFT)
        | ((core_id & CORE_ID_MASK) << CORE_ID_SHIFT)
        | (instr_id & INSTR_ID_MASK)
    )


def decompose_instr_id(composite_id: int) -> tuple:
    # Reverse of compose_instr_id: unpack composite-> (instr_id, core_id, type)
    instr_id = composite_id & INSTR_ID_MASK
    core_id = (composite_id >> CORE_ID_SHIFT) & CORE_ID_MASK
    core_type_id = (composite_id >> CORE_TYPE_ID_SHIFT) & CORE_TYPE_ID_MASK
    return instr_id, core_id, core_type_id


def get_reg_value(reg_name: str, instr_params: str) -> int:
    patterns = [
        rf"{reg_name}:X[0-9]{{1,2}}=(?:0x)?\s*([0-9a-f]+)",
        rf"{reg_name}:(?:0x)?\s*([0-9a-f]+)",
    ]
    for pattern in patterns:
        val = re.search(pattern, instr_params)
        if val is None:
            continue
        return int(val[1], 16)
    raise RuntimeError(f"Can't find {reg_name} value")


def get_param_value(param_name: str, instr_params: str) -> Optional[str]:
    val = re.compile(rf"{param_name}: ?([0-9A-Za-z]*)").search(instr_params)
    if val is not None:
        return val[1]
    return None


# Prepends char to make the string length divisable by n
def prepend_for_len_divisibility(value: str, n: int, char="0"):
    return value.rjust((len(value) + n - 1) // n * n, char)


# Example input: 0x12345678
# Example output (if bytes are hex-represented): 78 56 34 12
def hex_to_bytearray_reversed(hex_str: str) -> bytearray:
    return bytearray.fromhex(hex_str[2:])[::-1]


def asdict_nocopy(obj, cond=lambda x: True):
    if hasattr(obj, "__dict__"):
        return {key: asdict_nocopy(value, cond) for key, value in obj.__dict__.items() if cond(value)}
    return obj


def get_instr_length(instr_text: str) -> int:
    # Instruction encoding size: VF_SIMT and stallCyc instructions are 8 bytes
    # (dual-slot encoding), all others are 4 bytes (single-slot).
    # Examples:
    #   ADD X29, X29, X15                                       -> 4
    #   END                                                     -> 4
    #   VF_SIMT X2, X0                                          -> 8
    #   SIMT_S2R R3, P7, SUBBLOCKDIM, waitBitMask:1, stallCyc:1 -> 8
    if "VF_SIMT" in instr_text:
        return 8
    if "stallCyc:" in instr_text:
        return 8
    return 4


def read_file_line(file_path: str, line_num: int) -> Optional[str]:
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
            if 1 <= line_num <= len(lines):
                return lines[line_num - 1].strip()
            else:
                return None
    except FileNotFoundError:
        log.debug(f"File {file_path} not found")
        return None
    except Exception as e:
        log.debug(f"Failed to read line {line_num} from file {file_path}:\n{e}")
        return None


def short_hash(value: str, length: int = 6) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return digest[:length]


def parse_core_id_spec(core_id_filter: str, available_cores: List[int]) -> List[int]:
    if core_id_filter is None or core_id_filter == "all":
        return available_cores

    result: Set[int] = set()
    for part in core_id_filter.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            result.update(range(int(start_s), int(end_s) + 1))
        else:
            result.add(int(part))
    filtered_cores = sorted(result & set(available_cores))
    return filtered_cores


def get_tick_to_us_coeff() -> float:
    if biprof_ctx.no_ticks_to_ns:
        # Optional behavior: Blindly treat 1 hardware tick as
        # exactly 1 nanosecond. Then simply divide by
        # 1000 to get microseconds for Chrome Tracing.
        return TICK_TO_US_COEFF
    # Default behavior: Convert hardware ticks to real
    # nanoseconds based on device frequency.
    # 1 tick = (1 / freq_ghz) nanoseconds.
    # Chrome Tracing expects microseconds, so we
    # divide the result by 1000.
    freq_ghz = frequency_ghz_map[biprof_ctx.arch]
    return 1 / (freq_ghz * 1000)
