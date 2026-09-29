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

import gzip
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Any, Dict, List, Tuple, Union
from npusim.prof_demo.enums import CoreType, InstrType, InstrQueueType, CTSectionType
from npusim.prof_demo.analysis.vf import VF_CLASS_SCALAR, VF_CLASS_EXECUTION, VF_CLASS_LOAD_STORE
from npusim.prof_demo.analysis.dependencies import PipeDependencyCategory
from npusim.prof_demo.database import database_handler, BackendDBManager
from npusim.prof_demo.config import config
from npusim.prof_demo.utils import asdict_nocopy, decompose_instr_id, get_tick_to_us_coeff, ChromeTracingUtils

TraceId = Union[int, str]
EVENT_PHASE_COMPLETE = "X"
EVENT_PHASE_INSTANT = "i"
EVENT_PHASE_METADATA = "M"
EVENT_PHASE_COUNTER = "C"
EVENT_PHASE_FLOW_BEGIN = "s"
EVENT_PHASE_FLOW_END = "f"
CHROME_COLOR_RUNNING = "thread_state_running"
CHROME_COLOR_RAIL_ANIMATION = "rail_animation"
CHROME_COLOR_RAIL_RESPONSE = "rail_response"
CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED = "cq_build_attempt_passed"

INSTR_TO_QUEUE_TYPE = {
    InstrType.SCALAR: InstrQueueType.SCALAR,
    InstrType.CUBE: InstrQueueType.CUBE,
    InstrType.MTE1: InstrQueueType.MTE1,
    InstrType.MTE2: InstrQueueType.MTE2,
    InstrType.MTE3: InstrQueueType.MTE3,
    InstrType.FC: InstrQueueType.SCALAR,
    InstrType.FIXP: InstrQueueType.FIXP,
    InstrType.RVECEX: InstrQueueType.SIMD,
    InstrType.RVECLP: InstrQueueType.SIMD,
    InstrType.RVECSU: InstrQueueType.SIMD,
    InstrType.RVECST: InstrQueueType.SIMD,
    InstrType.RVECLD: InstrQueueType.SIMD,
    InstrType.PUSHQ: InstrQueueType.SIMD,
}

# Instruction families mirror doc/extend_params_keys.json::instr_keys.  The
# contract intentionally reuses flat key names between semantic groups, while
# the v4 report SQL obtained group scoping from separate LEFT JOIN tables.
# Keep that scoping explicit when one ExtendParams projection replaces them.
VECTOR_PARAM_INSTRS = (
    "RV_VLD",
    "RV_VLDI",
    "RV_VLDS",
    "RV_VST",
    "RV_VSTI",
    "RV_VSTS",
)
DMA_SIMPLE_PARAM_INSTRS = (
    "MOV_SRC_TO_DST_ALIGNv2",
    "MOV_UB_TO_L1",
    "MOV_L1_TO_UB",
    "MOV_UB_TO_UB",
)
DMA_MULTI_PARAM_INSTRS = (
    "MOV_OUT_TO_L1_MULTI_ND2NZ",
    "MOV_OUT_TO_L1_MULTI_DN2NZ",
)
DMA_2D_PARAM_INSTRS = ("LOAD_2Dv2", "LOAD_MX_2Dv2")
DMA_3D_PARAM_INSTRS = ("LOAD_L1_TO_DST_3DV2",)
DMA_ND_PARAM_INSTRS = ("ND_DMA",)
DMA_PARAM_INSTRS = (
    DMA_SIMPLE_PARAM_INSTRS + DMA_MULTI_PARAM_INSTRS + DMA_2D_PARAM_INSTRS + DMA_3D_PARAM_INSTRS + DMA_ND_PARAM_INSTRS
)
SIMT_MEM_PARAM_INSTRS = ("SIMT_LDG", "SIMT_LDS", "SIMT_STG", "SIMT_STS")
CUBE_PARAM_INSTRS = ("MMAD", "MMAD_MX")
FIXP_PARAM_INSTRS = ("FIX_L0C_TO_DST",)


def _group_scoped_projection(column: str, alias: str, instr_names: Tuple[str, ...]) -> str:
    """Project a flat JSON key only for its legacy source-table family."""
    sql_names = ", ".join("'" + name.replace("'", "''") + "'" for name in instr_names)
    return "CASE WHEN ExecutedInstructions.ExecInstrName IN (" + sql_names + f") THEN EP.{column} END AS {alias}"


@dataclass
class TracingInstrArgs:
    unit: TraceId
    task_id: str
    pc: str
    source: str
    line: str


@dataclass
class InstructionTracingArgs(TracingInstrArgs):
    def setattrs(self, attrs: List[Tuple[str, Union[None, bool, int, str, List[str]]]]):
        for attr in attrs:
            key, value = attr[0], attr[1]
            if value is not None:
                assert not self.__dict__.get(key, None), (
                    f"Overriding instruction field '{key}' detected. "
                    f"Previous value was = {self.__dict__[key]}, "
                    f"new value is {value}."
                )
                self.__dict__[key] = value


@dataclass
class TraceBaseEvent:
    name: str
    pid: TraceId
    tid: str
    ph: str


@dataclass
class TickStartEvent(TraceBaseEvent):
    ts: float
    dur: float


@dataclass
class CountEvent(TraceBaseEvent):
    ts: float
    cname: str
    args: Any


@dataclass
class InstructionTracing(TraceBaseEvent):
    ts: float
    dur: int
    cname: str
    args: TracingInstrArgs


@dataclass
class FlowEvent(TraceBaseEvent):
    bp: str
    ts: float
    id: int
    cat: str


@dataclass
class MetaEvent(TraceBaseEvent):
    args: Dict[str, Any]


@dataclass
class FlowEventDetails:
    event_id: int
    event_name: str
    flow_end: bool
    category_id: Optional[int]


class DatabaseManager:
    @staticmethod
    def get_cur_core_types(core_id: int) -> List[int]:
        units_cur = database_handler.execute(
            """
        SELECT DISTINCT CoreTypeId
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId=:core_id
        """,
            {"core_id": core_id},
        )
        cur_core_types = [unit_tuple[0] for unit_tuple in units_cur.fetchall()]
        core_full_order = [
            CoreType.AIC.value,
            CoreType.AIV0.value,
            CoreType.AIV1.value,
        ]
        return [x for x in core_full_order if x in cur_core_types]

    @staticmethod
    def get_cur_pipes(core_id: int, core_type_id: int) -> List[int]:
        pipes_cur = database_handler.execute(
            """
        SELECT DISTINCT InstrTypeId
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId=:core_id
        AND CoreTypeId=:core_type_id
        ORDER BY InstrTypeId ASC
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        )
        return [pipe_tuple[0] for pipe_tuple in pipes_cur.fetchall()]

    @staticmethod
    def get_unit_utilization_events(core_id: int) -> List[Any]:
        states = database_handler.execute(
            """
        SELECT *
        FROM UnitUtilization
        WHERE CoreId=:core_id
        """,
            {"core_id": core_id},
        )
        return states.fetchall()

    @staticmethod
    def get_instrs_by_unit_pipe(core_id: int, core_type_id: int, pipe_id: int) -> List[Any]:
        msg = f"""
        SELECT ExecutedInstructions.SourceInstrAddr AS Addr,
                ExecutedInstructions.ExecInstrId AS Id,
                ExecutedInstructions.ExecInstrTickStart AS Start,
                ExecutedInstructions.ExecInstrTickEnd AS End,
                ExecutedInstructions.ExecInstrName AS Name,
                ExecutedInstructions.InstrTypeId AS TypeId,
                ExecutedInstructions.ExecInstrIsSynchronization
                    AS IsSynchronization,
                ExecutedInstructions.ExecInstrVfClass AS VfClass,
                {_group_scoped_projection("processed_bytes", "VecProcessedBytes", VECTOR_PARAM_INSTRS)},
                {_group_scoped_projection("processed_elements", "VecProcessedElements", VECTOR_PARAM_INSTRS)},
                {_group_scoped_projection("vector_utilization", "VectorUtilization", VECTOR_PARAM_INSTRS)},

                {_group_scoped_projection("burst_number", "DmaMovSimpleBurstNumber", DMA_SIMPLE_PARAM_INSTRS)},
                {_group_scoped_projection("burst_len", "DmaMovSimpleBurstLength", DMA_SIMPLE_PARAM_INSTRS)},
                {_group_scoped_projection("src_stride", "DmaMovSimpleSrcStride", DMA_SIMPLE_PARAM_INSTRS)},
                {_group_scoped_projection("dst_stride", "DmaMovSimpleDstStride", DMA_SIMPLE_PARAM_INSTRS)},
                {_group_scoped_projection("sub_block_id", "DmaMovSubBlockId", DMA_SIMPLE_PARAM_INSTRS)},

                {_group_scoped_projection("n", "DmaMovMultiN", DMA_MULTI_PARAM_INSTRS)},
                {_group_scoped_projection("d", "DmaMovMultiD", DMA_MULTI_PARAM_INSTRS)},

                {_group_scoped_projection("m_start_pos", "DmaMovLoad2dMStartPos", DMA_2D_PARAM_INSTRS)},
                {_group_scoped_projection("k_start_pos", "DmaMovLoad2dKStartPos", DMA_2D_PARAM_INSTRS)},
                {_group_scoped_projection("m_step", "DmaMovLoad2dMStep", DMA_2D_PARAM_INSTRS)},
                {_group_scoped_projection("k_step", "DmaMovLoad2dKStep", DMA_2D_PARAM_INSTRS)},

                {_group_scoped_projection("m_step", "DmaMovLoad3dMStep", DMA_3D_PARAM_INSTRS)},
                {_group_scoped_projection("k_step", "DmaMovLoad3dKStep", DMA_3D_PARAM_INSTRS)},
                {_group_scoped_projection("dst_mem", "DmaMovLoad3dDst", DMA_3D_PARAM_INSTRS)},

                {_group_scoped_projection("loop0_size", "DmaMovNdDmaLoop0Size", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop1_size", "DmaMovNdDmaLoop1Size", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop2_size", "DmaMovNdDmaLoop2Size", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop3_size", "DmaMovNdDmaLoop3Size", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop4_size", "DmaMovNdDmaLoop4Size", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop0_lp_size", "DmaMovNdDmaLoop0LpSize", DMA_ND_PARAM_INSTRS)},
                {_group_scoped_projection("loop0_rp_size", "DmaMovNdDmaLoop0RpSize", DMA_ND_PARAM_INSTRS)},

                {_group_scoped_projection("processed_bytes", "DmaProcessedBytes", DMA_PARAM_INSTRS)},
                -- Preserve the v5 ExtendParams JSON presentation in trace output;
                -- the main analysis table retains the full binary REAL value.
                CASE
                    WHEN DmaMovProcessedBytes.MemoryTraffic IS NULL THEN NULL
                    ELSE CAST(printf('%.15g', DmaMovProcessedBytes.MemoryTraffic) AS REAL)
                END AS DmaMemoryTraffic,
                {_group_scoped_projection("processed_bytes", "SIMTLdStProcessedBytes", SIMT_MEM_PARAM_INSTRS)},

                {_group_scoped_projection("m", "MmadM", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("k", "MmadK", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("n", "MmadN", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_a_dtype", "MatrixADtype", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_b_dtype", "MatrixBDtype", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_c_dtype", "MatrixCDtype", CUBE_PARAM_INSTRS)},
                NULL AS EnabledMode,
                {_group_scoped_projection("hw_checks_unit_flag", "HwChecksUnitFlag", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("hw_sets_unit_flag", "HwSetsUnitFlag", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("gemv_mode_enable", "GEMVModeEnable", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_c_in_bias_table_buffer", "MatrixCInBiasTableBuffer", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_c_init_val", "MatrixCInitVal", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_a_processed_bytes", "MatrixAProcessedBytes", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_b_processed_bytes", "MatrixBProcessedBytes", CUBE_PARAM_INSTRS)},
                {_group_scoped_projection("matrix_c_processed_bytes", "MatrixCProcessedBytes", CUBE_PARAM_INSTRS)},

                {_group_scoped_projection("n", "FixpN", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("m", "FixpM", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("l2_cache_ctrl_mode", "FixpL2CacheCtrlMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("hw_checks_unit_flag", "FixpHwChecksUnitFlag", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("hw_sets_unit_flag", "FixpHwSetsUnitFlag", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("pre_quant_mode", "FixpPreQuantMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("pre_relu_mode", "FixpPreReluMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("pre_clip_relu_mode", "FixpPreClipReluMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("f32_channel_split_enable", "FixpF32ChannelSplitEnable", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("post_clip_relu_mode", "FixpPostClipReluMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("post_relu_mode", "FixpPostReluMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("nz2nd_enable", "FixpNZ2NDEnable", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("nz2dn_enable", "FixpNZ2DNEnable", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("c0_padding_enable", "FixpC0PaddingEnable", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("dst_mode", "FixpDstMode", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("dst_ub_mask", "FixpDstUBMask", FIXP_PARAM_INSTRS)},
                {_group_scoped_projection("processed_bytes", "FixpProcessedBytes", FIXP_PARAM_INSTRS)},

                EP.branch_efficiency AS BranchEfficiency,

                ScalarIpcDynamic.IPC AS ScalarIPC,

                VfIPC.ScalarIPC AS VFScalarIPC,
                VfIPC.ExecIPC AS VFExecIPC,
                VfIPC.LdStIPC AS VFLdStIPC,

                VfIPCDynamic.ScalarIPC AS InVFScalarIPC,
                VfIPCDynamic.ExecIPC AS InVFExecIPC,
                VfIPCDynamic.LdStIPC AS InVFLdStIPC,

                VfSimtIPC.ExecIPC AS VFSimtExecIPC,
                VfSimtIPC.LdStIPC AS VFSimtLdStIPC,
                VfSimtIPC.BranchIPC AS VFSimtBranchIPC
        FROM ExecutedInstructions
        LEFT JOIN main.ExtendParamsProjection AS EP
        ON ExecutedInstructions.ExecInstrId=EP.ExecInstrId
        LEFT JOIN main.DmaMovProcessedBytes
        ON ExecutedInstructions.ExecInstrId=DmaMovProcessedBytes.ExecInstrId
        LEFT JOIN ScalarIpcDynamic
        ON ScalarIpcDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN main.VfIPC
        ON VfIPC.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VfIPCDynamic
        ON VfIPCDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN main.VfSimtIPC
        ON VfSimtIPC.ExecInstrId=ExecutedInstructions.ExecInstrId
        WHERE ExecutedInstructions.ExecInstrCoreId=:core_id AND
              ExecutedInstructions.CoreTypeId=:core_type_id AND
              ExecutedInstructions.InstrTypeId=:pipe_id
        """
        res = database_handler.execute(
            msg,
            {
                "core_id": core_id,
                "core_type_id": core_type_id,
                "pipe_id": pipe_id,
            },
        )
        return res.fetchall()

    @staticmethod
    def get_sync_public_params() -> Dict[int, List[Tuple[str, Any]]]:
        """同步指令的公共语义 arg，由 helper 的 SYNC_BUF/SYNC_FLAG decoder 落库。

        取代从 ExecInstrParams 的 "PIPE:" / "bufId:" / "cnt:" 解析——那是原始
        operand 串。数据从一次性加载的 ExtendParams 投影读取，不回退解析 raw。

        sync_buffer_id 是 AscendC::Mutex::Lock/Unlock 的 MutexID。
        docs/zh/best_practices/matmul_sync_buffer.md 的分析步骤 3 要求用户比较两个
        pipe 是否使用同一个 buffer id，因此它必须留在流水图的参数里。
        """
        rows = database_handler.execute("""
        SELECT ExecInstrId AS Id, target_pipe AS TargetPipe,
               buf_id AS BufferId, buf_cnt AS OccupancyCount,
               flag_id AS FlagId, consumer_pipe AS ConsumerPipe
        FROM main.ExtendParamsProjection
        WHERE sync_kind IS NOT NULL
        """).fetchall()
        res: Dict[int, List[Tuple[str, Any]]] = {}
        for row in rows:
            attrs: List[Tuple[str, Any]] = []
            if row["TargetPipe"] != "UNKNOWN":
                attrs.append(("target_pipe", row["TargetPipe"]))
            # BAR 没有 buffer 参数，落 NULL；此处不能用 or/truthy 判断，
            # BufferId 与 OccupancyCount 取 0 都是合法值。
            if row["BufferId"] is not None:
                attrs.append(("sync_buffer_id", row["BufferId"]))
            if row["OccupancyCount"] is not None:
                attrs.append(("sync_buffer_round", row["OccupancyCount"]))
            if row["FlagId"] is not None:
                attrs.append(("sync_flag_id", row["FlagId"]))
            if row["ConsumerPipe"] is not None:
                attrs.append(("sync_consumer_pipe", row["ConsumerPipe"]))
            if attrs:
                res[row["Id"]] = attrs
        return res

    @staticmethod
    def get_pipe_events_maps() -> Dict[int, FlowEventDetails]:
        details_map: Dict[int, FlowEventDetails] = {}
        flow_events = database_handler.execute("""
            SELECT * FROM main.PipeDependency
            """).fetchall()
        for event in flow_events:
            instr_id = event["ExecInstrId"]
            details = FlowEventDetails(event["EventId"], event["EventName"], event["FlowEnd"], event["CategoryId"])
            details_map[instr_id] = details
        return details_map


class IBaseTraceBuilder:
    # core_id -> process id counter
    _process_id_counters: Dict[int, int] = defaultdict(int)

    def __init__(self) -> None:
        self.freq_coeff = get_tick_to_us_coeff()

    @classmethod
    def get_next_process_id(cls, core_id: int) -> int:
        prev_id = cls._process_id_counters[core_id]
        cls._process_id_counters[core_id] += 1
        return prev_id

    @staticmethod
    def make_instr_track_name(
        core_type: CoreType, group_queue: InstrQueueType, pipe_name: str, rel_order: CTSectionType = CTSectionType.INSTR
    ) -> str:
        section_name = f"{group_queue.name.lower()}_{rel_order.name.lower()}"
        return f"{core_type.name}_{pipe_name} ({section_name})"

    @staticmethod
    def make_aux_track_name(core_type: CoreType, track_name: str, section_name: str) -> str:
        return f"{core_type.name}_{track_name} ({section_name})"

    @staticmethod
    def process_name_event(pid: TraceId, process_name: str) -> MetaEvent:
        return MetaEvent("process_name", pid, "", EVENT_PHASE_METADATA, {"name": process_name})

    @staticmethod
    def thread_name_event(pid: TraceId, tid: str) -> MetaEvent:
        return MetaEvent("thread_name", pid, tid, EVENT_PHASE_METADATA, {"name": tid})

    @staticmethod
    def events_asdict(events: List[Any]) -> List[Any]:
        return [asdict_nocopy(event, lambda x: x is not None) for event in events]


class InstrTraceBuilder(IBaseTraceBuilder):
    def __init__(self):
        super().__init__()
        self.event_details_map = DatabaseManager.get_pipe_events_maps()
        self.sync_public_params_map = DatabaseManager.get_sync_public_params()

    def set_inline_instrs_lists(self, instrs: List[Any]) -> List[List[Any]]:
        instrs.sort(key=lambda x: x["Start"])
        inline_instrs: List[List[Any]] = []

        for instr in instrs:
            added_to_pipeline = False
            for line in inline_instrs:
                prev_end_ns = (line[-1]["Start"] + max(line[-1]["End"] - line[-1]["Start"], 1)) * self.freq_coeff
                if prev_end_ns < instr["Start"] * self.freq_coeff:
                    line.append(instr)
                    added_to_pipeline = True
                    break

            if not added_to_pipeline:
                inline_instrs.append([instr])

        return inline_instrs

    def get_instr_event_phase(self, duration_ticks: int) -> str:
        if duration_ticks > 1:
            return EVENT_PHASE_COMPLETE
        if config.no_ticks_to_ns:
            return EVENT_PHASE_COMPLETE
        return EVENT_PHASE_INSTANT

    def get_color(self, instr: Any) -> str:
        if instr["IsSynchronization"]:
            return CHROME_COLOR_RUNNING
        return ""

    def get_instruction_event(self, pid: TraceId, tid_name: str, instr: Any) -> TraceBaseEvent:
        # temporary hack to make flow events visible
        duration_ticks = max(1, instr["End"] - instr["Start"])

        args: InstructionTracingArgs = InstructionTracingArgs(
            pid,
            tid_name,
            hex(instr["Addr"]),
            None,
            None,
        )

        # Common instruction attributes.
        # 不再输出 encoding：32 位原始 ISA 编码不对外披露，且在存活的报告路径上
        # 它只用于显示，没有分析用途。
        instr_attrs = [
            ("instr_id", decompose_instr_id(instr["Id"])),
        ]
        # 同步指令的 target_pipe / sync_buffer_id / sync_buffer_round 来自
        # helper 的 SyncPublicParams，不再从 arguments 字符串里解析。
        instr_attrs.extend(self.sync_public_params_map.get(instr["Id"], ()))

        instr_type = InstrType(instr["TypeId"])

        if instr_type == InstrType.SCALAR:
            instr_attrs.append(
                ("ipc_value", ChromeTracingUtils.round_or_none(instr["ScalarIPC"], 5)),
            )
        # SIMD instruction attributes; SIMT leaf events are filtered upstream.
        elif instr_type in [InstrType.RVECEX, InstrType.RVECLP, InstrType.RVECSU, InstrType.RVECST, InstrType.RVECLD]:
            vec_util = ChromeTracingUtils.percents_or_none(instr["VectorUtilization"])
            branch_efficiency = ChromeTracingUtils.percents_or_none(instr["BranchEfficiency"])
            instr_attrs.extend(
                [
                    ("branch_efficiency", branch_efficiency),
                    ("vector_utilization", vec_util),
                    ("processed_bytes", instr["VecProcessedBytes"]),
                    ("processed_elements", instr["VecProcessedElements"]),
                    ("simt_processed_bytes", instr["SIMTLdStProcessedBytes"]),
                ]
            )

            rvec_ipc_attr = None
            if instr["VfClass"] == VF_CLASS_SCALAR:
                rvec_ipc_attr = ("scalar_ipc", ChromeTracingUtils.round_or_none(instr["InVFScalarIPC"], 5))
            elif instr["VfClass"] == VF_CLASS_EXECUTION:
                rvec_ipc_attr = ("exec_ipc", ChromeTracingUtils.round_or_none(instr["InVFExecIPC"], 5))
            elif instr["VfClass"] == VF_CLASS_LOAD_STORE:
                rvec_ipc_attr = ("load_store_ipc", ChromeTracingUtils.round_or_none(instr["InVFLdStIPC"], 5))
            if rvec_ipc_attr is not None:
                instr_attrs.append(rvec_ipc_attr)

        # MTE (DMA MOV) instruction attributes
        elif instr_type in [InstrType.MTE1, InstrType.MTE2, InstrType.MTE3, InstrType.PUSHQ]:
            instr_attrs.extend(
                [
                    ("burst_number", instr["DmaMovSimpleBurstNumber"]),
                    ("burst_length_bytes", instr["DmaMovSimpleBurstLength"]),
                    ("src_stride_bytes", instr["DmaMovSimpleSrcStride"]),
                    ("dst_stride_bytes", instr["DmaMovSimpleDstStride"]),
                    ("sub_block_id", instr["DmaMovSubBlockId"]),
                    ("n", instr["DmaMovMultiN"]),
                    ("d", instr["DmaMovMultiD"]),
                    ("m_start_pos_bytes", instr["DmaMovLoad2dMStartPos"]),
                    ("k_start_pos_bytes", instr["DmaMovLoad2dKStartPos"]),
                    ("m_step_bytes", instr["DmaMovLoad2dMStep"]),
                    ("k_step_bytes", instr["DmaMovLoad2dKStep"]),
                    ("m_step_3d_elements", instr["DmaMovLoad3dMStep"]),
                    ("k_step_3d_elements", instr["DmaMovLoad3dKStep"]),
                    ("dst", instr["DmaMovLoad3dDst"]),
                    ("loop0_size", instr["DmaMovNdDmaLoop0Size"]),
                    ("loop1_size", instr["DmaMovNdDmaLoop1Size"]),
                    ("loop2_size", instr["DmaMovNdDmaLoop2Size"]),
                    ("loop3_size", instr["DmaMovNdDmaLoop3Size"]),
                    ("loop4_size", instr["DmaMovNdDmaLoop4Size"]),
                    ("loop0_lp_size", instr["DmaMovNdDmaLoop0LpSize"]),
                    ("loop0_rp_size", instr["DmaMovNdDmaLoop0RpSize"]),
                    ("processed_bytes", instr["DmaProcessedBytes"]),
                    ("memory_traffic_gb_sec", instr["DmaMemoryTraffic"]),
                ]
            )
        # CUBE instruction attributes
        elif instr_type == InstrType.CUBE:
            instr_attrs.extend(
                [
                    ("matrix_dim_m", instr["MmadM"]),
                    ("matrix_dim_n", instr["MmadN"]),
                    ("matrix_dim_k", instr["MmadK"]),
                    ("matrix_a_dtype", instr["MatrixADtype"]),
                    ("matrix_b_dtype", instr["MatrixBDtype"]),
                    ("matrix_c_dtype", instr["MatrixCDtype"]),
                    ("enabled_mode", instr["EnabledMode"]),
                    ("matrix_c_in_bias_table_buf", instr["MatrixCInBiasTableBuffer"]),
                    ("hw_checks_unit_flag", instr["HwChecksUnitFlag"]),
                    ("hw_sets_unit_flag", instr["HwSetsUnitFlag"]),
                    ("gemv_mode_enable", instr["GEMVModeEnable"]),
                    ("matrix_c_init_val", instr["MatrixCInitVal"]),
                    ("matrix_a_processed_bytes", instr["MatrixAProcessedBytes"]),
                    ("matrix_b_processed_bytes", instr["MatrixBProcessedBytes"]),
                    ("matrix_c_processed_bytes", instr["MatrixCProcessedBytes"]),
                ]
            )
        # FIXP intruction attributes
        elif instr_type == InstrType.FIXP:
            target_ubs = None
            if instr["FixpDstUBMask"] == 0b01:
                target_ubs = "0"
            elif instr["FixpDstUBMask"] == 0b10:
                target_ubs = "1"
            elif instr["FixpDstUBMask"] == 0b11:
                target_ubs = "0, 1"

            instr_attrs.extend(
                [
                    ("matrix_dim_m", instr["FixpM"]),
                    ("matrix_dim_n", instr["FixpN"]),
                    ("l2_cache_ctrl_mode", instr["FixpL2CacheCtrlMode"]),
                    ("hw_checks_unit_flag", instr["FixpHwChecksUnitFlag"]),
                    ("hw_sets_unit_flag", instr["FixpHwSetsUnitFlag"]),
                    ("pre_quant_mode", instr["FixpPreQuantMode"]),
                    ("pre_relu_mode", instr["FixpPreReluMode"]),
                    ("pre_clip_relu_mode", instr["FixpPreClipReluMode"]),
                    ("f32_channel_split_enable", instr["FixpF32ChannelSplitEnable"]),
                    ("post_clip_relu_mode", instr["FixpPostClipReluMode"]),
                    ("post_relu_mode", instr["FixpPostReluMode"]),
                    ("nz2nd_enable", instr["FixpNZ2NDEnable"]),
                    ("nz2dn_enable", instr["FixpNZ2DNEnable"]),
                    ("c0_padding_enable", instr["FixpC0PaddingEnable"]),
                    ("dst_mode", instr["FixpDstMode"]),
                    ("target_ubs", target_ubs),
                    ("processed_bytes", instr["FixpProcessedBytes"]),
                ]
            )
        if instr["Name"] in ["VF", "VFI"]:
            instr_attrs.extend(
                [
                    ("scalar_ipc", ChromeTracingUtils.round_or_none(instr["VFScalarIPC"], 5)),
                    ("exec_ipc", ChromeTracingUtils.round_or_none(instr["VFExecIPC"], 5)),
                    ("load_store_ipc", ChromeTracingUtils.round_or_none(instr["VFLdStIPC"], 5)),
                ]
            )
        elif instr["Name"] == "VF_SIMT":
            instr_attrs.extend(
                [
                    ("exec_ipc", ChromeTracingUtils.round_or_none(instr["VFSimtExecIPC"], 5)),
                    ("load_store_ipc", ChromeTracingUtils.round_or_none(instr["VFSimtLdStIPC"], 5)),
                    ("branch_ipc", ChromeTracingUtils.round_or_none(instr["VFSimtBranchIPC"], 5)),
                ]
            )

        args.setattrs(instr_attrs)
        ts = instr["Start"] * self.freq_coeff
        duration_us = duration_ticks * self.freq_coeff
        event_phase = self.get_instr_event_phase(duration_ticks)
        color = self.get_color(instr)

        return InstructionTracing(instr["Name"], pid, tid_name, event_phase, ts, duration_us, color, args)

    def get_bar_instr_event(self, pid: TraceId, tid_name: str, instr: Any) -> TraceBaseEvent:
        args = TracingInstrArgs(pid, tid_name, hex(instr["Addr"]), None, None)
        duration_ticks = max(1, instr["End"] - instr["Start"])
        ts = instr["Start"] * self.freq_coeff
        duration_us = duration_ticks * self.freq_coeff
        color = self.get_color(instr)
        return InstructionTracing(instr["Name"], pid, tid_name, EVENT_PHASE_COMPLETE, ts, duration_us, color, args)

    def get_instr_events(self, inline_instrs, pid: TraceId, pipe: str, track_name: Optional[str] = None):
        res = []
        for tid, thread_line in enumerate(inline_instrs):
            if len(thread_line) == 0:
                continue
            tid_name = f"{pipe}_SLOT{tid:0>2}"
            res.append(self.thread_name_event(pid, tid_name))
            for instr in thread_line:
                if instr["Name"].lower().startswith("bar"):
                    res.append(self.get_bar_instr_event(pid, tid_name, instr))
                else:
                    res.append(self.get_instruction_event(pid, tid_name, instr))

                if instr["Id"] in self.event_details_map:
                    details = self.event_details_map[instr["Id"]]
                    pipe_flow_event = self.get_pipe_flow_event(pid, tid_name, instr["Start"], details)
                    res.append(pipe_flow_event)

        return res

    def get_start_event(self, pid: TraceId, pipe: str, suffix: str):
        tid = f"{pipe}_{suffix}"
        return TickStartEvent(
            "TICK_START_EVENT",
            pid,
            tid,
            EVENT_PHASE_COMPLETE,
            0,
            0,
        )

    def get_scalar_trace(self, core_id: int, core_type_id: int) -> List[Any]:
        res = []
        instrs = DatabaseManager.get_instrs_by_unit_pipe(core_id, core_type_id, InstrType.SCALAR.value)
        ldst_instrs = []
        any_instrs = []
        for instr in instrs:
            if instr["Name"].lower().startswith("ld") or instr["Name"].lower().startswith("st"):
                ldst_instrs.append(instr)
            else:
                any_instrs.append(instr)
        ldst_instrs = self.set_inline_instrs_lists(ldst_instrs)
        any_instrs = self.set_inline_instrs_lists(any_instrs)
        core_type = CoreType(core_type_id)
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        ldst_track_name = self.make_instr_track_name(core_type, InstrQueueType.SCALAR, "SCALAR_LDST")
        res.append(self.process_name_event(pid, ldst_track_name))
        res += self.get_instr_events(ldst_instrs, pid, "SCALAR_LDST")
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        t_name = f"{InstrType.SCALAR}"
        track_name = self.make_instr_track_name(core_type, InstrQueueType.SCALAR, "SCALAR")
        res.append(self.process_name_event(pid, track_name))
        res += self.get_instr_events(any_instrs, pid, "SCALAR")
        res.append(self.get_start_event(pid, t_name, "SLOT00"))
        return self.events_asdict(res)

    def get_pipe_trace(self, core_id: int, core_type_id: int, pipe_id: int) -> List[Any]:
        res: List[Any] = []
        instrs = DatabaseManager.get_instrs_by_unit_pipe(core_id, core_type_id, pipe_id)
        # Preserve legacy behavior: SIMT leaf instructions are not displayed.
        instrs = [i for i in instrs if not (50 <= pipe_id <= 54 and i["Name"].startswith("SIMT_"))]
        instrs = self.set_inline_instrs_lists(instrs)

        group = INSTR_TO_QUEUE_TYPE[InstrType(pipe_id)]
        core_type = CoreType(core_type_id)
        t_name = InstrType(pipe_id).name
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        track_name = self.make_instr_track_name(core_type, group, t_name)
        res.append(self.process_name_event(pid, track_name))
        res += self.get_instr_events(instrs, pid, t_name)
        return self.events_asdict(res)

    def get_pipe_flow_event(self, pid, tid, start_tick, details):
        category = "flags" if details.category_id == PipeDependencyCategory.FLAGS.value else "buffers"
        return FlowEvent(
            details.event_name,
            pid,
            tid,
            EVENT_PHASE_FLOW_END if details.flow_end else EVENT_PHASE_FLOW_BEGIN,
            "e" if details.flow_end else "",
            start_tick * self.freq_coeff,
            details.event_id,
            f"Pipe dependency ({category})",
        )

    def get_json_obj(self, core_id):
        events = []
        for core_type in DatabaseManager.get_cur_core_types(core_id):
            for pipe in DatabaseManager.get_cur_pipes(core_id, core_type):
                if pipe == InstrType.SCALAR.value:
                    events.extend(self.get_scalar_trace(core_id, core_type))
                else:
                    events.extend(self.get_pipe_trace(core_id, core_type, pipe))
        return events


def core_events(core_id):
    IBaseTraceBuilder._process_id_counters.clear()
    builder = InstrTraceBuilder()
    events = builder.get_json_obj(core_id)
    rows = DatabaseManager.get_unit_utilization_events(core_id)
    for core_type in CoreType:
        selected = [r for r in rows if r["CoreTypeId"] == core_type.value]
        if not selected:
            continue
        pid = builder.get_next_process_id(core_id)
        name = builder.make_aux_track_name(core_type, "UNIT_UTILIZATION", "unit_utilization")
        events.extend(
            builder.events_asdict(
                [
                    builder.process_name_event(pid, name),
                    builder.thread_name_event(pid, "unit_utilization"),
                ]
            )
        )
        for row in selected:
            events.append(
                asdict_nocopy(
                    CountEvent(
                        "unit_utilization",
                        pid,
                        "unit_utilization",
                        EVENT_PHASE_COUNTER,
                        int(row["Tick"]) * builder.freq_coeff,
                        CHROME_COLOR_RAIL_ANIMATION,
                        {"Utilization (%)": round(float(row["Util"]) * 100, 2)},
                    )
                )
            )
    # Drop metadata-only tracks, while retaining all timed events.
    active = {(e["pid"], e["tid"]) for e in events if "ts" in e}
    pids = {pid for pid, _ in active}
    return [
        e
        for e in events
        if "ts" in e
        or (e["name"] == "process_name" and e["pid"] in pids)
        or (e["name"] == "thread_name" and (e["pid"], e["tid"]) in active)
    ]


def divide_json(events, limit):
    """Split timed events; repeat metadata so each shard is self-contained.

    Like the original report, the limit is a payload target, not a hard cap:
    metadata and a single oversized event may exceed it.
    """
    if limit == -1 or len(json.dumps(events)) < limit:
        return [events]
    metadata = [e for e in events if "ts" not in e]
    blocks, block, size = [], [], 0
    for event in events:
        if "ts" not in event:
            continue
        event_size = len(json.dumps(event))
        if block and size + event_size > limit:
            blocks.append(metadata + block)
            block, size = [], 0
        block.append(event)
        size += event_size
    if block:
        blocks.append(metadata + block)
    return blocks


GZIP_THRESHOLD = 5242880


def generate(output_dir):
    for core in BackendDBManager.get_active_cores():
        events = core_events(core)
        if not events:
            raise ValueError(f"No trace events for core {core}")
        for index, block in enumerate(divide_json(events, config.json_max_size)):
            path = os.path.join(output_dir, f"core_{core}_tracing_report_{index}.json")
            text = json.dumps({"traceEvents": block, "displayTimeUnit": "ns"}, allow_nan=False)
            if len(text) > GZIP_THRESHOLD:
                with gzip.open(path + ".gz", "xt", encoding="utf-8") as stream:
                    stream.write(text)
            else:
                with open(path, "x", encoding="utf-8") as stream:
                    stream.write(text)
