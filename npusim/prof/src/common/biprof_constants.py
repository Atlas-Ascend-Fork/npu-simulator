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

import re
from typing import Any, Dict

from npusim.prof.src.common.biprof_enum import ArchName, CoreType, InstrType
from npusim.prof.src.common.biprof_enum import MemoryType


DAVID_CACHE_LINE_SIZE = 128

supported_archs = [ArchName.V100]

unit_map = {ArchName.V100: [CoreType.AIC, CoreType.AIV0, CoreType.AIV1]}

frequency_ghz_map = {
    ArchName.V100: 1.65,
}

CONTRACT_ARCH_ALIASES: Dict[str, ArchName] = {
    "ascend950pr_9599": ArchName.V100,
    "v100": ArchName.V100,
}


def get_arch_instr_pattern_map() -> Dict[Any, Any]:
    info_pat = r"\[info\]"
    tick_pat = r"\[(?P<tick>[0-9]+)\]"
    pc_pat = r"\(PC: (?P<pc>0x[0-9a-f]+)\)"
    pipe_pat = r"(?P<pipe>[A-Z0-3]+)"
    enc_pat = r"\(Binary: (?P<encoding>0x[0-9a-f ]+)\)"
    name_pat = r"(?P<name>\w+)"
    params_pat = r"(?P<params>.*)"
    id_pat = r"\(ID: (?P<id>[0-9]+)\)"
    trigger_pipe = (
        r"(?P<trigger_pipe_params>.*PIPE:"
        r"(?P<source_pipe>[a-zA-Z0-3]+),"
        r"\s+TRIGGER PIPE:"
        r"(?P<trigger_pipe>[a-zA-Z0-3]+).*)"
    )

    return {
        ArchName.V100: re.compile(
            rf"^{info_pat}\s+{tick_pat}\s+"
            rf"{pc_pat}\s+{pipe_pat}\s+:\s+"
            rf"{enc_pat}\s+{id_pat}\s+{name_pat}"
            rf"\s+({trigger_pipe}|{params_pat})\s*$"
        ),
    }


execution_end_pattern = re.compile(
    r"\s*\+{22}\s*core:\s*\d+.*"
    r"start:\s*(?P<start>\d+),.*"
    r"rtsqId:\s*(?P<rtsqId>\d+),.*"
    r"streamId:\s*(?P<streamId>\d+),.*"
    r"taskId:\s*(?P<taskId>\d+),.*"
    r"subtaskId:\s*(?P<subtaskId>\d+),.*"
    r"threadId:\s*(?P<threadId>\d+),"
)

instr_log_file_pattern = re.compile(
    r"(?:ca|pem|core(?P<core_id>\d+))\."
    r"(?:vec|cube)core(?P<subcore_id>\d+)\."
    r"instr_log.dump"
)

coretype_signs_mapping = {
    "cubecore0": CoreType.AIC,
    "veccore0": CoreType.AIV0,
    "veccore1": CoreType.AIV1,
}


UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "N/A"
MAX_SIMT_WARPS = 32
MAX_SIMT_THREADS_PER_WARP = 32

DEFAULT_VL_VALUE = 0x100  # = 256 (bytes)

WAIT_INSTRS = ("barrier", "set_event", "wait_event", "BAR", "SET_FLAG", "WAIT_FLAG")

SYNCHRO_INSTRS = (
    "SET_FLAG",
    "WAIT_FLAG",
    "GET_BUF",
    "RLS_BUF",
    "SET_INTRA_BLOCK",
    "WAIT_INTRA_BLOCK",
    "SET_CROSS_CORE",
    "WAIT_FLAG_DEVI",
)

RELTIME_THRESHOLD = 0.5

TAIL_EFFECT_HINT_THRESHOLD = 0.10

MEMORY_ACCESS_READ = "R"
MEMORY_ACCESS_WRITE = "W"

MAX_UINT_32 = 4294967295

UB_SIZE_BYTES = 0x40000  # 256 KB
L0A_SIZE_BYTES = 0x10000  # 64 KB
L0B_SIZE_BYTES = 0x10000  # 64 KB
L0C_SIZE_BYTES = 0x40000  # 256 KB
L1_SIZE_BYTES = 0x80000  # 512 KB
OUT_SIZE_BYTES = 0x80000  # 512 KB

memory_size_map = {
    ArchName.V100: {
        MemoryType.AIV0_UB: UB_SIZE_BYTES,
        MemoryType.AIV1_UB: UB_SIZE_BYTES,
        MemoryType.AIC_L0A: L0A_SIZE_BYTES,
        MemoryType.AIC_L0B: L0B_SIZE_BYTES,
        MemoryType.AIC_L0C: L0C_SIZE_BYTES,
        MemoryType.AIC_L1: L1_SIZE_BYTES,
        MemoryType.OUT: OUT_SIZE_BYTES,
    }
}

BYTES_IN_GB = 1024 * 1024 * 1024
KB_IN_GB = BYTES_IN_GB / 1024

# We treat 1 tick as 1 nanosecond.
# Since Chrome Tracing requires timestamps in microseconds,
# convert nanoseconds to microseconds using this coefficient.
TICK_TO_US_COEFF = 1 / 1000

MEM_UTIL_WIDTH = 32
MEM_UTIL_HEIGHT = 64

AIV_SAMPLING_PIPES = [
    InstrType.MTE2,
    InstrType.MTE3,
    InstrType.PUSHQ,
]

SAMPLING_PIPES_TO_SUBCORES = {
    CoreType.AIC: [
        InstrType.MTE2,
        InstrType.MTE1,
        InstrType.CUBE,
        InstrType.FIXP,
        InstrType.MTE3,
    ],
    CoreType.AIV0: AIV_SAMPLING_PIPES,
    CoreType.AIV1: AIV_SAMPLING_PIPES,
}

SUPPORTED_MEM_TYPES = [
    MemoryType.AIV0_UB,
    MemoryType.AIV1_UB,
    MemoryType.AIC_L1,
    MemoryType.AIC_L0A,
    MemoryType.AIC_L0B,
    MemoryType.AIC_L0C,
]


VfInstrTypes = {
    "Scalar": {
        InstrType.RVECSU,
    },
    "Execution": {
        InstrType.RVECEX,
    },
    "Load/Store": {
        InstrType.RVECLD,
        InstrType.RVECST,
    },
}

VfSimtInstrTypes = {
    "Execution": {
        InstrType.RVECEX,
    },
    "Load/Store": {
        InstrType.RVECLD,
        InstrType.RVECST,
    },
    "Branch": {
        InstrType.RVECLP,
    },
}

# TFLOPS
peak_tflops_map: Dict[ArchName, Dict[str, float]] = {
    ArchName.V100: {
        "cube_fp16_bf16": 432,
        "cube_tf32_hf32": 216,
        "cube_fp32": 26,
        "cube_int8_fp8": 864,
        "vector_fp16_bf16": 27,
        "vector_fp16_FMA": 54,
        "vector_fp32": 13.5,
        "vector_int32": 13.5,
    },
}

core_num_map = {
    ArchName.V100: 32,
}

# TB/s * 1024 = GB/s
peak_bandwidth_map: Dict[ArchName, Dict[str, float]] = {
    ArchName.V100: {
        "OUT": 1.6 * 1024,
        "L2": 5.3 * 1024,
    },
}


V100_SIMD_STALL_REASONS = set(
    [
        "SAME_VF_TID_REQUIRED",
        "LD_GS_ISSUED_CNT_LIMIT",
        "LD_ISSUED_CNT_LIMIT",
        "EX_ISSUED_CNT_LIMIT",
        "ST_ISSUED_CNT_LIMIT",
        "SRC_NOT_READY_VREG",
        "SRC_NOT_READY_PREG",
        "SMEM_BARRIER",
        "SEND_BARRIER",
        "VSQZN_FIFO_FULL",
        "VSQZN_FIFO_EMPTY",
        "EXQ0_ALMOST_FULL",
        "EXQ1_ALMOST_FULL",
        "SHQ_TO_EXQ0_ISSUE_LIMIT",
        "SHQ_TO_EXQ1_ISSUE_LIMIT",
        "EXQ_ISSUED_CNT_LIMIT",
        "SRC_PORT_LIMIT",
        "DST_PORT_LIMIT",
        "VALU_GRP_CONFLICT_ACTIVE_MD_AVL",
        "VALU_GRP_CONFLICT_ACTIVE_MD",
        "VALU_GRP_OUTPUT_ACTIVE_MD_AVL",
        "VALU_GRP_OUTPUT_ACTIVE_MD",
        "VALU_THRPUT_CFLT",
        "VALU_RESC_CFLT",
        "INORDER_ISSUED_CNT_LIMIT",
        "INORDER_ISSUE_SEQUENCE",
        "INORDER_RETIRE_SEQUENCE",
        "WREG_RAW_HAZARD",
        "STALL_BY_3SRC_VREG",
        "STALL_BY_3SRC_PREG",
        "MUL_FU_INPUT_CFLT",
        "MOV_FU_INPUT_CFLT",
        "EQUALIZER_VALU_GROUP_CFLT",
        "EQUALIZER_INORDER_INSTR_CFLT",
        "INTERFACE_FULL",
        "NONE",
    ]
)
