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

import gzip
import os
import logging as log
from typing import Optional, Any, Dict, List, Tuple, Union
from dataclasses import dataclass, asdict
from abc import abstractmethod
import json
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed

from npusim.prof.src.common.biprof_enum import (
    CoreType,
    MemoryType,
    InstrType,
    ICacheEventType,
    CTSectionType,
    InstrQueueType,
)
from npusim.prof.src.common.biprof_constants import (
    SYNCHRO_INSTRS,
    SAMPLING_PIPES_TO_SUBCORES,
    VfSimtInstrTypes,
)
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.database_manager import BackendDBManager
from npusim.prof.src.common.logger import final_logger
from npusim.prof.src.common.utils import (
    asdict_nocopy,
    decompose_instr_id,
    get_tick_to_us_coeff,
)
from npusim.prof.src.common.stage_reporter import StageReporter
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.backend.utils import (
    CallStackUtils,
    ChromeTracingUtils,
)
from npusim.prof.src.backend.analyzers.pipe_dependency_analyzer import (
    PipeDependencyCategory,
)
from npusim.prof.src.common.biprof_constants import (
    memory_size_map,
    VfInstrTypes,
)

TraceId = int

# ---------- Instruction exclusion config ----------
_EXCLUDE_TOML_PATH: Optional[str] = os.path.join(os.path.dirname(__file__), "..", "..", "..", "exclude_instr.toml")
if not os.path.isfile(_EXCLUDE_TOML_PATH):
    _EXCLUDE_TOML_PATH = None
# ---------- End instruction exclusion config ----------

# Event types supported by Chrome tracing
EVENT_PHASE_BEGIN = "B"
EVENT_PHASE_END = "E"
EVENT_PHASE_COMPLETE = "X"
EVENT_PHASE_INSTANT = "i"
EVENT_PHASE_NESTABLE_ASYNC_BEGIN = "b"
EVENT_PHASE_NESTABLE_ASYNC_END = "e"
EVENT_PHASE_NESTABLE_ASYNC_INSTANT = "n"
EVENT_PHASE_FLOW_BEGIN = "s"
EVENT_PHASE_FLOW_STEP = "t"
EVENT_PHASE_FLOW_END = "f"
EVENT_PHASE_METADATA = "M"
EVENT_PHASE_COUNTER = "C"
EVENT_PHASE_SAMPLE = "P"
EVENT_PHASE_CREATE_OBJECT = "N"
EVENT_PHASE_SNAPSHOT_OBJECT = "O"
EVENT_PHASE_DELETE_OBJECT = "D"
EVENT_PHASE_MARK = "R"
EVENT_PHASE_CLOCK_SYNC = "c"

# Event specific parameters supported by Chrome tracing
EVENT_PHASE_INSTANT_SCOPE_THREAD = "t"

# Reserved color names supported by Chrome tracing
CHROME_COLOR_GOOD = "good"
CHROME_COLOR_BAD = "bad"
CHROME_COLOR_GREY = "grey"
CHROME_COLOR_TERRIBLE = "terrible"
CHROME_COLOR_RAIL_RESPONSE = "rail_response"
CHROME_COLOR_RAIL_ANIMATION = "rail_animation"
CHROME_COLOR_CQ_BUILD_ATTEMPT_PASSED = "cq_build_attempt_passed"
CHROME_COLOR_RUNNING = "thread_state_running"
CHROME_COLOR_IO_WAIT = "thread_state_iowait"
CHROME_COLOR_BUILD_FAILED = "cq_build_failed"


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


@dataclass
class TracingInstrArgs:
    unit: TraceId
    task_id: str
    pc: str
    source: str
    line: str


@dataclass
class ICacheEventArgs(TracingInstrArgs):
    event_type: str
    size: str


@dataclass
class QueueFullEventArgs:
    instr: str
    instr_id: int


@dataclass
class CacheRefillEventArgs:
    addr: str
    size: int
    exec_unit: str


@dataclass
class DCacheScalarBiuRequestEventArgs:
    addr: str
    size: str
    pc: Optional[str]
    instr_text: str


@dataclass
class DCacheScalarHitMissArgs:
    addr: str
    pc: str
    instr_text: str


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

    def __repr__(self) -> str:
        repr_str = super().__repr__()
        dynamic_attrs = {key: getattr(self, key) for key in self.__dict__ if key not in self.__dataclass_fields__}
        if dynamic_attrs:
            repr_str += f", {dynamic_attrs}"
        return repr_str


@dataclass
class TraceBaseEvent:
    name: str
    pid: TraceId
    tid: int
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
class DispatchEvent(TraceBaseEvent):
    ts: float
    s: str


@dataclass
class RegionNode:
    core_type: int
    region_type: int
    pipe: int
    name: str
    start: int
    end: int
    warp_id: int


@dataclass
class RegionTracing(TraceBaseEvent):
    ts: float
    dur: float


@dataclass
class ICacheReadEvent(TraceBaseEvent):
    ts: float
    cname: str
    s: str
    args: ICacheEventArgs


@dataclass
class StallEvent(TraceBaseEvent):
    ts: int
    dur: int
    cname: str
    args: Union[QueueFullEventArgs, CacheRefillEventArgs]


@dataclass
class FlowEvent(TraceBaseEvent):
    bp: str
    ts: float
    id: int
    cat: str


@dataclass
class BubbleEventArgs:
    main_pipeline: str
    sub_type: str
    category: str
    reason: str
    optimizable: bool
    optimization_hint: str
    waited_pipeline: str
    tick_start: int
    tick_end: int


@dataclass
class BubbleEvent(TraceBaseEvent):
    ts: float
    dur: float
    args: BubbleEventArgs


@dataclass
class MetaEvent(TraceBaseEvent):
    args: Dict[str, Any]


@dataclass
class DCacheRefillEvent(TraceBaseEvent):
    ts: int
    dur: int
    cname: str
    args: DCacheScalarBiuRequestEventArgs


@dataclass
class DCacheHitMissEvent(TraceBaseEvent):
    ts: int
    cname: str
    s: str
    args: DCacheScalarHitMissArgs


@dataclass
class WarpEventArgs:
    warp_utilization: str


@dataclass
class WarpEvent:
    name: str
    pid: int
    tid: int
    ts: float
    dur: float
    args: WarpEventArgs
    ph: str = "X"


@dataclass
class FlowEventDetails:
    event_id: int
    event_name: str
    flow_end: bool
    category_id: Optional[int]


class WarpUtilTraceBuilder:
    @staticmethod
    def get_name() -> str:
        return "warp_utilization"

    @staticmethod
    def get_json_obj(core_id: int) -> List[Any]:
        res: List[Any] = []
        freq_coeff = get_tick_to_us_coeff()
        warps = database_handler.execute(
            """
        SELECT SIMTBlockId AS BlockId,
               WarpId, TickStart AS Start, Duration,
               WarpUtilization AS Util
        FROM SIMTWarpUtilization
        WHERE CoreId=?
        """,
            (core_id,),
        ).fetchall()
        seen_pids: set = set()
        seen_tids: Dict[int, set] = defaultdict(set)
        for warp in warps:
            pid = int(warp["BlockId"])
            tid = int(warp["WarpId"])
            if pid not in seen_pids:
                res.append(
                    asdict(
                        MetaEvent(
                            "process_name",
                            pid,
                            0,
                            EVENT_PHASE_METADATA,
                            {"name": f"Process SIMT Block {pid}"},
                        )
                    )
                )
                seen_pids.add(pid)
            if tid not in seen_tids[pid]:
                res.append(
                    asdict(
                        MetaEvent(
                            "thread_name",
                            pid,
                            tid,
                            EVENT_PHASE_METADATA,
                            {"name": f"Warp{tid}"},
                        )
                    )
                )
                seen_tids[pid].add(tid)
            name = f"Warp{warp['WarpId']}"
            ts = warp["Start"] * freq_coeff
            duration_us = warp["Duration"] * freq_coeff
            res.append(
                asdict(
                    WarpEvent(
                        name=name,
                        pid=pid,
                        tid=tid,
                        ts=ts,
                        dur=duration_us,
                        args=WarpEventArgs(warp_utilization=f"{warp['Util'] * 100:.1f}%"),
                    )
                )
            )
        return res


class DatabaseManager:
    @staticmethod
    def get_active_core_list() -> List[int]:
        return BackendDBManager.get_active_cores()

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
    def get_bubble_events(core_id: int, core_type_id: int) -> List[Any]:
        if not database_handler.table_exists("TraceBubbles"):
            return []
        return database_handler.execute(
            """
        SELECT TickStart, TickEnd, Category, SubType,
               MainPipeline, Reason, Optimizable,
               OptimizationHint, WaitedPipeline, GapTicks
        FROM TraceBubbles
        WHERE CoreId=:core_id AND CoreTypeId=:core_type_id
        """,
            {"core_id": core_id, "core_type_id": core_type_id},
        ).fetchall()

    @staticmethod
    def get_ub_utilization_events(core_id: int) -> List[Any]:
        states = database_handler.execute(
            """
        SELECT *
        FROM MemoryUtilizationStates
        WHERE CoreId=:core_id
        """,
            {"core_id": core_id},
        )
        return states.fetchall()

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
    def get_issue_queue_utilization_events(core_id: int) -> List[Any]:
        states = database_handler.execute(
            """
        SELECT *
        FROM IssueQueueUtilization
        WHERE CoreId=:core_id
        """,
            {"core_id": core_id},
        )
        return states.fetchall()

    @staticmethod
    def get_exec_queue_utilization_events(core_id: int) -> List[Any]:
        states = database_handler.execute(
            """
        SELECT *
        FROM ExecQueueUtilization
        WHERE CoreId=:core_id
        """,
            {"core_id": core_id},
        )
        return states.fetchall()

    @staticmethod
    def get_icache_events(core_id: int, core_type_id: int) -> List[Any]:
        req = """
        SELECT ICacheEvents.EventTimeTick AS Time,
                ICacheEvents.SourceInstrAddr AS Addr,
                SourceFiles.SourceFilePath AS Path,
                DebugInfo.DebugInfoLineNum AS LineNum,
                SourceLines.SourceLine AS Line,
                ICacheEvents.EventSize AS Size,
                ICacheEvents.HitStatus AS HS,
                ICacheEvents.ICacheEventType as EventType
        FROM ICacheEvents
        LEFT JOIN SourceInstructions
                ON ICacheEvents.SourceInstrAddr=
                SourceInstructions.SourceInstrAddr
        LEFT JOIN DebugInfo
                ON SourceInstructions.DebugInfoId=DebugInfo.DebugInfoId
        LEFT JOIN SourceFiles
                ON DebugInfo.SourceFileId=SourceFiles.SourceFileId
        LEFT JOIN SourceLines
                ON SourceLines.SourceLineId=DebugInfo.SourceLineId
        WHERE ICacheEvents.CoreId=:core_id
        AND CoreTypeId=:core_type_id
        """
        events = database_handler.execute(req, {"core_id": core_id, "core_type_id": core_type_id}).fetchall()
        return events

    @staticmethod
    def get_sync_wait_times_map() -> Dict[int, int]:
        wait_times = database_handler.execute("""
        SELECT PipeDependency.ExecInstrId as ExecInstrId,
        ExecInstrTickStart - DispatchTimeTick AS DelayTime
        FROM PipeDependency LEFT JOIN DispatchTime
        ON PipeDependency.ExecInstrId == DispatchTime.ExecInstrId
        LEFT JOIN ExecutedInstructions
        ON PipeDependency.ExecInstrId == ExecutedInstructions.ExecInstrId
        WHERE FlowEnd == 1 AND DispatchTimeTick IS NOT NULL
        """).fetchall()
        res: Dict[int, int] = {}
        for instr in wait_times:
            res[int(instr["ExecInstrId"])] = int(instr["DelayTime"])
        return res

    @staticmethod
    def get_instrs_by_unit_pipe(core_id: int, core_type_id: int, pipe_id: int) -> List[Any]:
        msg = f"""
        SELECT ExecutedInstructions.SourceInstrAddr AS Addr,
                ExecutedInstructions.ExecInstrId AS Id,
                ExecutedInstructions.ExecInstrTickStart AS Start,
                ExecutedInstructions.ExecInstrTickEnd AS End,
                ExecutedInstructions.ExecInstrName AS Name,
                ExecutedInstructions.ExecInstrParams AS Params,
                ExecutedInstructions.InstrTypeId AS TypeId,
                SourceInstructions.SourceInstrEncoding AS Encoding,
                SourceInstructions.SourceInstrText AS Text,
                CallStackId,
                SourceFiles.SourceFilePath
                || ":"
                || CAST(DebugInfo.DebugInfoLineNum as VARCHAR(10))
                                        AS SrcPath,
                SourceLines.SourceLine AS SrcLine,
                DebugInfo.DebugInfoId AS DebugInfoId,

                VectorUtilizations.ProcessedBytes AS VecProcessedBytes,
                VectorUtilizations.ProcessedElements AS VecProcessedElements,
                VectorUtilizations.VectorUtilization,

                DmaMovSimpleParams.BurstNumber AS DmaMovSimpleBurstNumber,
                DmaMovSimpleParams.BurstLength AS DmaMovSimpleBurstLength,
                DmaMovSimpleParams.SrcStride AS DmaMovSimpleSrcStride,
                DmaMovSimpleParams.DstStride AS DmaMovSimpleDstStride,
                DmaMovSimpleParams.SubBlockId AS DmaMovSubBlockId,

                DmaMovMultiParams.N AS DmaMovMultiN,
                DmaMovMultiParams.D AS DmaMovMultiD,

                DmaMovLoad2dParams.MStartPosition AS DmaMovLoad2dMStartPos,
                DmaMovLoad2dParams.MStartPosition AS DmaMovLoad2dKStartPos,
                DmaMovLoad2dParams.MStep AS DmaMovLoad2dMStep,
                DmaMovLoad2dParams.KStep AS DmaMovLoad2dKStep,

                DmaMovLoad3dParams.MStep AS DmaMovLoad3dMStep,
                DmaMovLoad3dParams.KStep AS DmaMovLoad3dKStep,
                DmaMovLoad3dParams.Dst AS DmaMovLoad3dDst,

                DmaMovNdDmaParams.Loop0Size AS DmaMovNdDmaLoop0Size,
                DmaMovNdDmaParams.Loop1Size AS DmaMovNdDmaLoop1Size,
                DmaMovNdDmaParams.Loop2Size AS DmaMovNdDmaLoop2Size,
                DmaMovNdDmaParams.Loop3Size AS DmaMovNdDmaLoop3Size,
                DmaMovNdDmaParams.Loop4Size AS DmaMovNdDmaLoop4Size,
                DmaMovNdDmaParams.Loop0LpSize AS DmaMovNdDmaLoop0LpSize,
                DmaMovNdDmaParams.Loop0RpSize AS DmaMovNdDmaLoop0RpSize,

                DmaMovProcessedBytes.ProcessedBytes AS DmaProcessedBytes,
                DmaMovProcessedBytes.MemoryTraffic AS DmaMemoryTraffic,
                SIMTLdStProcessedBytes.ProcessedBytes
                                             AS SIMTLdStProcessedBytes,

                UBBankConflicts.ReadConflicts,
                UBBankConflicts.WriteConflicts,

                UbBankConflictsCross.ConflictCount AS ConflictCount,
                UbBankConflictsCross.ConflictInstrs AS ConflictInstrs,

                SIMTGmAccessMetrics.CacheLineCount,
                SIMTGmAccessMetrics.CacheLineUtilization,
                SIMTGmAccessMetrics.MemAccessStride,

                MmadParams.M AS MmadM,
                MmadParams.K AS MmadK,
                MmadParams.N AS MmadN,
                MmadParams.MatrixADtype,
                MmadParams.MatrixBDtype,
                MmadParams.MatrixCDtype,
                MmadParams.EnabledMode,
                MmadParams.MatrixAL0AAddress,
                MmadParams.MatrixBL0BAddress,
                MmadParams.MatrixCAddress,
                MmadParams.HwChecksUnitFlag,
                MmadParams.HwSetsUnitFlag,
                MMadParams.GEMVModeEnable,
                MMadParams.MatrixCInBiasTableBuffer,
                MMadParams.MatrixCInitVal,
                MMadParams.MatrixAProcessedBytes,
                MMadParams.MatrixBProcessedBytes,
                MMadParams.MatrixCProcessedBytes,

                FixpParams.N AS FixpN,
                FixpParams.M AS FixpM,
                FixpParams.L2CacheCtrlMode AS FixpL2CacheCtrlMode,
                FixpParams.HwChecksUnitFlag AS FixpHwChecksUnitFlag,
                FixpParams.HwSetsUnitFlag AS FixpHwSetsUnitFlag,
                FixpParams.PreQuantMode AS FixpPreQuantMode,
                FixpParams.PreReluMode AS FixpPreReluMode,
                FixpParams.PreClipReluMode AS FixpPreClipReluMode,
                FixpParams.F32ChannelSplitEnable AS FixpF32ChannelSplitEnable,
                FixpParams.PostClipReluMode AS FixpPostClipReluMode,
                FixpParams.PostReluMode AS FixpPostReluMode,
                FixpParams.NZ2NDEnable AS FixpNZ2NDEnable,
                FixpParams.NZ2DNEnable AS FixpNZ2DNEnable,
                FixpParams.C0PaddingEnable AS FixpC0PaddingEnable,
                FixpParams.DstMode AS FixpDstMode,
                FixpParams.DstUBMask AS FixpDstUBMask,
                FixpParams.ProcessedBytes AS FixpProcessedBytes,

                SIMTExecutionMasks.BranchEfficiency,
                SubQueueTypes.SubQueueType AS SubQueueType,

                ScalarIpcDynamic.IPC AS ScalarIPC,

                VfIPC.ScalarIPC AS VFScalarIPC,
                VfIPC.ExecIPC AS VFExecIPC,
                VfIPC.LdStIPC AS VFLdStIPC,

                VfIPCDynamic.ScalarIPC AS InVFScalarIPC,
                VfIPCDynamic.ExecIPC AS InVFExecIPC,
                VfIPCDynamic.LdStIPC AS InVFLdStIPC,

                VfSimtIPC.ExecIPC AS VFSimtExecIPC,
                VfSimtIPC.LdStIPC AS VFSimtLdStIPC,
                VfSimtIPC.BranchIPC AS VFSimtBranchIPC,

                VfSimtIPCDynamic.ExecIPC AS InVFSimtExecIPC,
                VfSimtIPCDynamic.LdStIPC AS InVFSimtLdStIPC,
                VfSimtIPCDynamic.BranchIPC AS InVFSimtBranchIPC
        FROM ExecutedInstructions
        {"INNER" if biprof_ctx.object_file else "LEFT"} JOIN SourceInstructions
        ON ExecutedInstructions.SourceInstrAddr=
            SourceInstructions.SourceInstrAddr
        LEFT JOIN DebugInfo
        ON SourceInstructions.DebugInfoId=DebugInfo.DebugInfoId
        LEFT JOIN SourceLines
        ON DebugInfo.SourceLineId=SourceLines.SourceLineId
        LEFT JOIN SourceFiles
        ON DebugInfo.SourceFileId=SourceFiles.SourceFileId
        LEFT JOIN SubQueueTypes
        ON SubQueueTypes.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VectorUtilizations
        ON ExecutedInstructions.ExecInstrId=VectorUtilizations.ExecInstrId
        LEFT JOIN UBBankConflicts
        ON ExecutedInstructions.ExecInstrId=UBBankConflicts.ExecInstrId
        LEFT JOIN UbBankConflictsCross
        ON ExecutedInstructions.ExecInstrId
            =UbBankConflictsCross.ExecInstrId
        LEFT JOIN DmaMovProcessedBytes
        ON DmaMovProcessedBytes.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN DmaMovSimpleParams
        ON DmaMovSimpleParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN DmaMovLoad2dParams
        ON DmaMovLoad2dParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN DmaMovLoad3dParams
        ON DmaMovLoad3dParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN DmaMovMultiParams
        ON DmaMovMultiParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN DmaMovNdDmaParams
        ON DmaMovNdDmaParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN SIMTGmAccessMetrics
        ON SIMTGmAccessMetrics.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN SIMTLdStProcessedBytes
        ON SIMTLdStProcessedBytes.ExecInstrId
           =ExecutedInstructions.ExecInstrId
        LEFT JOIN MmadParams
        ON MmadParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN FixpParams
        ON FixpParams.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN ScalarIpcDynamic
        ON ScalarIpcDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VfIPC
        ON VfIPC.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VfIPCDynamic
        ON VfIPCDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VfSimtIPC
        ON VfSimtIPC.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN VfSimtIPCDynamic
        ON VfSimtIPCDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN SIMTExecutionMasks
        ON SIMTExecutionMasks.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN CallStackIDs
        ON CallStackIDs.SourceInstrAddr = SourceInstructions.SourceInstrAddr
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
    def get_instrs_with_dispatch(core_id: int, core_type_id: int, instr_type_id: int) -> List[Any]:
        if not database_handler.table_exists("DispatchTime"):
            return []
        res = database_handler.execute(
            """
        SELECT ExecutedInstructions.ExecInstrId AS Id,
               ExecInstrName AS Name,
               DispatchTimeTick AS Dispatch,
               ExecInstrTickStart AS Start
        FROM ExecutedInstructions
        INNER JOIN DispatchTime
        ON ExecutedInstructions.ExecInstrId=DispatchTime.ExecInstrId
        WHERE ExecInstrCoreId=:core_id AND CoreTypeId=:core_type_id
              AND InstrTypeId=:instr_type_id
        """,
            {
                "core_type_id": core_type_id,
                "core_id": core_id,
                "instr_type_id": instr_type_id,
            },
        ).fetchall()
        return res

    @staticmethod
    def get_spr_data() -> Dict[int, List[Tuple[str, str]]]:
        sprs = database_handler.execute("""
        SELECT ExecInstrId AS Id,
               SprName, SprValue
        FROM SprInfoPerInstr
        """).fetchall()
        res = defaultdict(list)
        for spr in sprs:
            res[spr["Id"]].append(("spr." + spr["SprName"].lower(), "0x" + spr["SprValue"]))
        return res

    @staticmethod
    def get_pipe_events_maps() -> Dict[int, FlowEventDetails]:
        details_map: Dict[int, FlowEventDetails] = {}
        if not database_handler.table_exists("PipeDependency"):
            return details_map

        flow_events = database_handler.execute("""
            SELECT * FROM PipeDependency
            """).fetchall()
        for event in flow_events:
            instr_id = event["ExecInstrId"]
            details = FlowEventDetails(
                event["EventId"],
                event["EventName"],
                event["FlowEnd"],
                event["CategoryId"],
            )
            details_map[instr_id] = details
        return details_map

    @staticmethod
    def get_dispatch_events_map() -> Dict[int, FlowEventDetails]:
        dispatch_events_map: Dict[int, FlowEventDetails] = {}
        if not database_handler.table_exists("DispatchTime"):
            return dispatch_events_map
        dispatch_events = database_handler.execute("""
        SELECT ExecutedInstructions.ExecInstrId,
               ExecInstrName AS Name
        FROM DispatchTime
        INNER JOIN ExecutedInstructions
        ON DispatchTime.ExecInstrId=ExecutedInstructions.ExecInstrId
        """).fetchall()
        for event in dispatch_events:
            instr_id = event["ExecInstrId"]
            details = FlowEventDetails(event["ExecInstrId"], event["Name"], True, None)
            dispatch_events_map[instr_id] = details
        return dispatch_events_map

    @staticmethod
    def get_core_regions(core_id: int) -> Tuple[List[RegionNode], List[RegionNode]]:
        custom_regions: List[Any] = list()
        auto_regions: List[Any] = list()
        if not database_handler.table_exists("Regions"):
            return custom_regions, auto_regions
        regions = database_handler.execute(
            """
            SELECT CoreTypeId, RegionStart, RegionEnd,
                   RegionCoreId, RegionTypeId,
                   InstrQueueTypeId, WarpId, RegionName
            FROM Regions
            INNER JOIN RegionNames ON
                Regions.RegionNameId=RegionNames.RegionNameId
            WHERE RegionCoreId=:core_id
            """,
            {"core_id": core_id},
        ).fetchall()
        for region in regions:
            core_type = region["CoreTypeId"]
            region_type = region["RegionTypeId"]
            region_pipe = region["InstrQueueTypeId"]
            region_warp_id = region["WarpId"]
            region_name = region["RegionName"]
            region_start = region["RegionStart"]
            region_end = region["RegionEnd"]
            if region_name.startswith("Auto"):
                region_name = f"{region_pipe}_{region_name[len('Auto') :]}"
                auto_regions.append(
                    RegionNode(
                        core_type,
                        region_type,
                        region_pipe,
                        region_name,
                        region_start,
                        region_end,
                        region_warp_id,
                    )
                )
            else:
                region_name = region_name[len("Custom") :]
                custom_regions.append(
                    RegionNode(
                        core_type,
                        region_type,
                        region_pipe,
                        region_name,
                        region_start,
                        region_end,
                        region_warp_id,
                    )
                )
        return custom_regions, auto_regions

    @staticmethod
    def get_queue_full_events(core_id: int, core_type_id: int) -> List[Any]:
        req = """
        SELECT  QueueFullEvents.ExecInstrId as InstrId, EventBeginTick,
                EventEndTick, ExecInstrName, InstrTypeName
        FROM QueueFullEvents
        LEFT JOIN ExecutedInstructions ON
            QueueFullEvents.ExecInstrId=ExecutedInstructions.ExecInstrId
        LEFT JOIN InstrTypes ON
            ExecutedInstructions.InstrTypeId=InstrTypes.InstrTypeId
        WHERE QueueFullEvents.CoreId=:core_id
        AND QueueFullEvents.CoreTypeId=:core_type_id
        """
        events = database_handler.execute(req, {"core_id": core_id, "core_type_id": core_type_id}).fetchall()
        return events

    @staticmethod
    def get_icache_refill_events(core_id: int, core_type_id: int) -> List[Any]:
        req = """
        SELECT SourceInstrAddr,
               CacheLineSize,
               ICacheEventType,
               EventBeginTick AS Start,
               EventEndTick AS End
        FROM ICacheRefillEvents
        WHERE CoreId=:core_id
        AND CoreTypeId=:core_type_id
        """
        events = database_handler.execute(req, {"core_id": core_id, "core_type_id": core_type_id}).fetchall()
        return events

    @staticmethod
    def get_dcache_refill_events(core_id: int, core_type_id: int) -> List[Any]:
        req = """
        SELECT DCacheBiuRequests.SourceInstrAddr AS SourceInstrAddr,
               SourceInstructions.SourceInstrText AS InstrText,
               DCacheBiuRequests.RequestSize AS Size,
               DCacheBiuRequests.CacheLineAddress AS Addr,
               DCacheBiuRequests.EventBeginTick AS Start,
               DCacheBiuRequests.EventEndTick AS End
        FROM DCacheBiuRequests
        LEFT JOIN SourceInstructions
        ON DCacheBiuRequests.SourceInstrAddr=SourceInstructions.SourceInstrAddr
        WHERE CoreId=:core_id
        AND CoreTypeId=:core_type_id
        """
        events = database_handler.execute(req, {"core_id": core_id, "core_type_id": core_type_id}).fetchall()
        return events

    @staticmethod
    def get_dcache_hit_miss_events(core_id: int, core_type_id: int) -> List[Any]:
        req = """
            SELECT DCacheHitMissEvents.SourceInstrAddr AS SourceInstrAddr,
                SourceInstructions.SourceInstrText AS InstrText,
                DCacheHitMissEvents.CacheLineAddress AS Addr,
                DCacheHitMissEvents.CoreId AS CoreId,
                DCacheHitMissEvents.CoreTypeId AS Subcore,
                DCacheHitMissEvents.IsHit AS IsHit,
                DCacheHitMissEvents.EventTick AS Start
            FROM DCacheHitMissEvents
            LEFT JOIN SourceInstructions
            ON DCacheHitMissEvents.SourceInstrAddr=
                SourceInstructions.SourceInstrAddr
            WHERE CoreId=:core_id AND
                  Subcore=:core_type_id;
        """
        events = database_handler.execute(req, {"core_id": core_id, "core_type_id": core_type_id}).fetchall()
        return events


class IBaseTraceBuilder:
    # core_id -> process id counter
    _process_id_counters: Dict[int, int] = defaultdict(int)
    # pid -> {thread_name: int tid}
    _thread_id_map: Dict[int, Dict[str, int]] = defaultdict(dict)
    # pid -> next tid counter
    _thread_id_counters: Dict[int, int] = defaultdict(int)

    def __init__(self) -> None:
        self.freq_coeff = get_tick_to_us_coeff()

    @classmethod
    def get_next_process_id(cls, core_id: int) -> int:
        prev_id = cls._process_id_counters[core_id]
        cls._process_id_counters[core_id] += 1
        return prev_id

    @classmethod
    def _resolve_tid(cls, pid: int, tid_name: str) -> int:
        name_to_tid = cls._thread_id_map[pid]
        if tid_name in name_to_tid:
            return name_to_tid[tid_name]
        tid = cls._thread_id_counters[pid]
        cls._thread_id_counters[pid] += 1
        name_to_tid[tid_name] = tid
        return tid

    @classmethod
    def register_thread(cls, pid: int, tid_name: str) -> Optional[MetaEvent]:
        name_to_tid = cls._thread_id_map[pid]
        if tid_name in name_to_tid:
            return None
        tid = cls._thread_id_counters[pid]
        cls._thread_id_counters[pid] += 1
        name_to_tid[tid_name] = tid
        return MetaEvent("thread_name", pid, tid, EVENT_PHASE_METADATA, {"name": tid_name})

    @staticmethod
    def make_instr_track_name(
        core_type: CoreType,
        group_queue: InstrQueueType,
        pipe_name: str,
        rel_order: CTSectionType = CTSectionType.INSTR,
    ) -> str:
        section_name = f"{group_queue.name.lower()}_{rel_order.name.lower()}"
        return f"{core_type.name}_{pipe_name} ({section_name})"

    @staticmethod
    def make_aux_track_name(core_type: CoreType, track_name: str, section_name: str) -> str:
        return f"{core_type.name}_{track_name} ({section_name})"

    @staticmethod
    def process_name_event(pid: TraceId, process_name: str) -> MetaEvent:
        return MetaEvent("process_name", pid, 0, EVENT_PHASE_METADATA, {"name": process_name})

    @staticmethod
    def thread_name_event(pid: TraceId, tid_name: str) -> MetaEvent:
        tid = IBaseTraceBuilder._resolve_tid(pid, tid_name)
        return MetaEvent("thread_name", pid, tid, EVENT_PHASE_METADATA, {"name": tid_name})

    @staticmethod
    def events_asdict(events: List[Any]) -> List[Any]:
        return [asdict_nocopy(event, lambda x: x is not None) for event in events]

    @staticmethod
    def make_prefix(
        core_type: CoreType,
        group_queue: InstrQueueType,
        rel_order: CTSectionType = CTSectionType.INSTR,
    ) -> str:
        return f"{core_type.name}_{group_queue.name}_{rel_order.name}_"

    @abstractmethod
    def get_json_obj(self, core_id: int) -> List[Any]:
        pass


class InstrTraceBuilder(IBaseTraceBuilder):
    CoreAndCoreTypeIdTuple = Tuple[int, int]
    WarpIdToListOfInstrsDict = Dict[int, List[Any]]
    SimtInstrsDict = Dict[CoreAndCoreTypeIdTuple, WarpIdToListOfInstrsDict]

    def __init__(self) -> None:
        super().__init__()
        self.event_details_map = DatabaseManager.get_pipe_events_maps()
        self.spr_data_map = DatabaseManager.get_spr_data()
        self.dispatch_events_map = DatabaseManager.get_dispatch_events_map()
        self.sync_wait_times_map = DatabaseManager.get_sync_wait_times_map()
        self.call_stack_events_map = CallStackUtils.get_call_stack_map()
        # Mapping: (core_id, core_type_id) -> warp_id -> list of instrs
        self.simt_instrs: InstrTraceBuilder.SimtInstrsDict = defaultdict(lambda: defaultdict(list))
        self.pipes: List[int] = [
            InstrType.RVECEX.value,
            InstrType.RVECLP.value,
            InstrType.RVECSU.value,
            InstrType.RVECST.value,
            InstrType.RVECLD.value,
        ]
        self._exclude_set = self._load_exclude_set()

    @staticmethod
    def _load_exclude_set() -> set:
        """Load instruction exclusion set from exclude_instr.toml."""
        if not _EXCLUDE_TOML_PATH:
            return set()
        try:
            with open(_EXCLUDE_TOML_PATH, "r", encoding="utf-8") as f:
                content = f.read()
            # Strip comment lines (# ...) before matching, to avoid
            # picking up keys inside example/comment blocks.
            lines = [line for line in content.splitlines() if not line.lstrip().startswith("#")]
            clean = "\n".join(lines)
            match = re.search(r"exclude_instr_list\s*=\s*\[(.*?)\]", clean, re.DOTALL)
            if not match:
                return set()
            items = re.findall(r'"([^"]*)"', match.group(1))
            if not items:
                items = re.findall(r"'([^']*)'", match.group(1))
            return set(items)
        except Exception as e:
            log.warning(f"Failed to read {_EXCLUDE_TOML_PATH}: {e}")
            return set()

    def filter_instructions(self, instrs: List[Any]) -> List[Any]:
        """Filter out instructions named in exclude_instr.toml."""
        if not self._exclude_set:
            return instrs

        filtered = []
        excluded_count = 0
        for instr in instrs:
            if instr["Name"] in self._exclude_set:
                excluded_count += 1
                continue
            filtered.append(instr)

        if excluded_count > 0:
            log.info(
                f"Filtering complete: Excluded {excluded_count} instructions "
                f"(Total: {len(instrs)}, Retained: {len(filtered)})"
            )
        return filtered

    # Stores SIMT instrs info into simt_instrs (warp_map)
    # Returns: non-SIMT instrs
    def prehandle_instrs(self, instrs: List[Any], core_id: int, core_type_id: int, pipe_id: int) -> List[Any]:
        if pipe_id not in self.pipes:
            return instrs
        res = list()
        for instr in instrs:
            if instr["Name"].startswith("SIMT_"):
                params = instr["Params"]
                if params is None:
                    res.append(instr)
                    continue
                match = re.findall(r"\[warpId:\s?(?P<warp_id>[0-9]+)\]", params)
                if len(match) != 1:
                    log.warning(f"SIMT instruction has no warpId: {dict(instr)}")
                    continue

                self.simt_instrs[(core_id, core_type_id)][int(match[0])].append(instr)
            else:
                res.append(instr)
        return res

    def get_simt_trace(self, core_id: int, core_type_id: int):
        return self.simt_instrs[(core_id, core_type_id)]

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
        if biprof_ctx.no_ticks_to_ns:
            return EVENT_PHASE_COMPLETE
        return EVENT_PHASE_INSTANT

    def get_color(self, instr: Any) -> str:
        if instr["Name"] in SYNCHRO_INSTRS:
            return CHROME_COLOR_RUNNING
        return ""

    def get_instruction_event(self, pid: TraceId, tid_name: str, instr: Any) -> TraceBaseEvent:
        # temporary hack to make flow events visible
        duration_ticks = max(1, instr["End"] - instr["Start"])

        args: InstructionTracingArgs = InstructionTracingArgs(
            pid,
            tid_name,
            hex(instr["Addr"]),
            instr["SrcPath"],
            instr["SrcLine"],
        )

        callstack = CallStackUtils.get_instruction_call_stack(self.call_stack_events_map, instr["CallStackId"])

        # Common instruction attributes
        instr_attrs = [
            ("instr_id", decompose_instr_id(instr["Id"])[0]),
            ("arguments", instr["Params"]),
            ("encoding", instr["Encoding"]),
        ]
        if instr["Id"] in self.spr_data_map:
            instr_attrs.extend(self.spr_data_map[instr["Id"]])

        instr_type = InstrType(instr["TypeId"])

        if instr_type == InstrType.SCALAR:
            instr_attrs.append(
                ("ipc_value", ChromeTracingUtils.round_or_none(instr["ScalarIPC"], 5)),
            )
        # SIMD/SIMT Vector instruction attributes
        elif instr_type in [
            InstrType.RVECEX,
            InstrType.RVECLP,
            InstrType.RVECSU,
            InstrType.RVECST,
            InstrType.RVECLD,
        ]:
            is_simt = instr["Name"].startswith("SIMT_")
            source_available = instr["Text"] is not None

            if is_simt and source_available:
                sourcename = instr["Text"].split(" ", 1)[0]
                if "." in sourcename:
                    datatype = sourcename.split(".")[1]
                    instr_attrs.append(("data_type", datatype))

            cache_util = ChromeTracingUtils.percents_or_none(instr["CacheLineUtilization"])
            vec_util = ChromeTracingUtils.percents_or_none(instr["VectorUtilization"])
            branch_efficiency = ChromeTracingUtils.percents_or_none(instr["BranchEfficiency"])
            instr_attrs.extend(
                [
                    ("cache_line_count", instr["CacheLineCount"]),
                    ("cache_line_utilization", cache_util),
                    ("memory_access_stride", instr["MemAccessStride"]),
                    ("branch_efficiency", branch_efficiency),
                    ("vector_utilization", vec_util),
                    ("processed_bytes", instr["VecProcessedBytes"]),
                    ("processed_elements", instr["VecProcessedElements"]),
                    ("ub_bank_conflicts_read", instr["ReadConflicts"]),
                    ("ub_bank_conflicts_write", instr["WriteConflicts"]),
                    ("simt_processed_bytes", instr["SIMTLdStProcessedBytes"]),
                ]
            )

            if is_simt:
                if instr_type in VfSimtInstrTypes["Execution"]:
                    rvec_ipc_attr = (
                        "exec_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFSimtExecIPC"], 5),
                    )
                elif instr_type in VfSimtInstrTypes["Load/Store"]:
                    rvec_ipc_attr = (
                        "load_store_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFSimtLdStIPC"], 5),
                    )
                else:  # elif instr_type in VfSimtInstrTypes["Branch"]
                    rvec_ipc_attr = (
                        "branch_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFSimtBranchIPC"], 5),
                    )
            else:
                if instr_type in VfInstrTypes["Scalar"]:
                    rvec_ipc_attr = (
                        "scalar_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFScalarIPC"], 5),
                    )
                elif instr_type in VfInstrTypes["Execution"]:
                    rvec_ipc_attr = (
                        "exec_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFExecIPC"], 5),
                    )
                else:  # elif instr_type in VfInstrTypes["Load/Store"]
                    rvec_ipc_attr = (
                        "load_store_ipc",
                        ChromeTracingUtils.round_or_none(instr["InVFLdStIPC"], 5),
                    )
            instr_attrs.append(rvec_ipc_attr)

        # MTE (DMA MOV) instruction attributes
        elif instr_type in [
            InstrType.MTE1,
            InstrType.MTE2,
            InstrType.MTE3,
            InstrType.PUSHQ,
        ]:
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
                    (
                        "matrix_a_l0a_address",
                        ChromeTracingUtils.hex_or_none(instr["MatrixAL0AAddress"]),
                    ),
                    (
                        "matrix_b_l0b_address",
                        ChromeTracingUtils.hex_or_none(instr["MatrixBL0BAddress"]),
                    ),
                    (
                        "matrix_c_address",
                        hex(instr["MatrixCAddress"]) if instr["MatrixCAddress"] is not None else None,
                    ),
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
                    (
                        "scalar_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFScalarIPC"], 5),
                    ),
                    (
                        "exec_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFExecIPC"], 5),
                    ),
                    (
                        "load_store_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFLdStIPC"], 5),
                    ),
                ]
            )
        elif instr["Name"] == "VF_SIMT":
            instr_attrs.extend(
                [
                    (
                        "exec_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFSimtExecIPC"], 5),
                    ),
                    (
                        "load_store_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFSimtLdStIPC"], 5),
                    ),
                    (
                        "branch_ipc",
                        ChromeTracingUtils.round_or_none(instr["VFSimtBranchIPC"], 5),
                    ),
                ]
            )
        if instr["ConflictCount"]:
            instr_attrs.extend(
                [
                    ("conflict_count", instr["ConflictCount"]),
                    ("conflict_instructions", instr["ConflictInstrs"]),
                ]
            )
        if instr["Id"] in self.sync_wait_times_map:
            instr_attrs.append(("wait_time", self.sync_wait_times_map[instr["Id"]]))

        instr_attrs.append(("stack", callstack))
        args.setattrs(instr_attrs)
        ts = instr["Start"] * self.freq_coeff
        duration_us = duration_ticks * self.freq_coeff
        event_phase = self.get_instr_event_phase(duration_ticks)
        color = self.get_color(instr)

        return InstructionTracing(
            instr["Name"],
            pid,
            self._resolve_tid(pid, tid_name),
            event_phase,
            ts,
            duration_us,
            color,
            args,
        )

    def get_bar_instr_event(self, pid: TraceId, tid_name: str, instr: Any) -> TraceBaseEvent:
        args = TracingInstrArgs(pid, tid_name, hex(instr["Addr"]), instr["SrcPath"], instr["SrcLine"])
        duration_ticks = max(1, instr["End"] - instr["Start"])
        ts = instr["Start"] * self.freq_coeff
        duration_us = duration_ticks * self.freq_coeff
        color = self.get_color(instr)
        return InstructionTracing(
            instr["Name"],
            pid,
            self._resolve_tid(pid, tid_name),
            EVENT_PHASE_COMPLETE,
            ts,
            duration_us,
            color,
            args,
        )

    def get_instr_events(self, inline_instrs, pid: TraceId, pipe: str, track_name: Optional[str] = None):
        res = []
        for slot_idx, thread_line in enumerate(inline_instrs):
            if len(thread_line) == 0:
                continue
            tid_name = f"{pipe}_SLOT{slot_idx:0>2}"
            res.append(self.thread_name_event(pid, tid_name))
            for instr in thread_line:
                if instr["Name"].lower().startswith("bar") and not tid_name.lower().startswith("all"):
                    res.append(self.get_bar_instr_event(pid, tid_name, instr))
                else:
                    res.append(self.get_instruction_event(pid, tid_name, instr))

                if instr["Id"] in self.event_details_map:
                    details = self.event_details_map[instr["Id"]]
                    pipe_flow_event = self.get_pipe_flow_event(pid, tid_name, instr["Start"], details)
                    res.append(pipe_flow_event)

                if instr["Id"] in self.dispatch_events_map:
                    details = self.dispatch_events_map[instr["Id"]]
                    dispatch_flow_event = self.get_dispatch_flow_event(pid, tid_name, instr["Start"], details)
                    res.append(dispatch_flow_event)

        return res

    def get_dispatch_events(self, pid: TraceId, pipe, core_id, core_type_id, instr_type_id) -> List[Any]:
        instrs = DatabaseManager.get_instrs_with_dispatch(core_id, core_type_id, instr_type_id)
        if not instrs:
            return []
        res: List[TraceBaseEvent] = [self.thread_name_event(pid, pipe)]
        tid = self._resolve_tid(pid, pipe)
        for instr in instrs:
            res.append(
                DispatchEvent(
                    name=instr["Name"],
                    pid=pid,
                    tid=tid,
                    ph=EVENT_PHASE_INSTANT,
                    ts=instr["Dispatch"] * self.freq_coeff,
                    s=EVENT_PHASE_INSTANT_SCOPE_THREAD,
                )
            )
            res.append(
                FlowEvent(
                    name=instr["Name"],
                    pid=pid,
                    tid=tid,
                    ph=EVENT_PHASE_FLOW_BEGIN,
                    bp="",
                    ts=instr["Dispatch"] * self.freq_coeff,
                    id=instr["Id"],
                    cat="Dispatch time",
                )
            )
        return res

    def get_start_event(self, pid: TraceId, pipe: str, suffix: str):
        tid_name = f"{pipe}_{suffix}"
        tid = self._resolve_tid(pid, tid_name)
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
        instrs = self.filter_instructions(instrs)
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
        res.append(self.thread_name_event(pid, f"{t_name}_SLOT00"))
        res.append(self.get_start_event(pid, t_name, "SLOT00"))
        return self.events_asdict(res)

    def get_pipe_trace(self, core_id: int, core_type_id: int, pipe_id: int) -> List[Any]:
        res: List[Any] = []
        instrs = DatabaseManager.get_instrs_by_unit_pipe(core_id, core_type_id, pipe_id)
        instrs = self.filter_instructions(instrs)
        instrs = self.prehandle_instrs(instrs, core_id, core_type_id, pipe_id)
        instrs = self.set_inline_instrs_lists(instrs)

        group = INSTR_TO_QUEUE_TYPE[InstrType(pipe_id)]
        core_type = CoreType(core_type_id)
        t_name = InstrType(pipe_id).name
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        track_name = self.make_instr_track_name(core_type, group, t_name)
        res.append(self.process_name_event(pid, track_name))
        res += self.get_instr_events(instrs, pid, t_name)

        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        t_name = f"{InstrType(pipe_id)}_DISPATCH"
        dispatch_track_name = self.make_instr_track_name(core_type, group, t_name, CTSectionType.PRE)
        dispatch_events = self.get_dispatch_events(pid, dispatch_track_name, core_id, core_type_id, pipe_id)
        if len(dispatch_events) != 0:
            res.append(self.process_name_event(pid, dispatch_track_name))
            res += dispatch_events

        return self.events_asdict(res)

    def get_pipe_flow_event(self, pid: TraceId, tid: str, start_tick: int, details: FlowEventDetails) -> FlowEvent:
        if details.flow_end:
            ph = EVENT_PHASE_FLOW_END
            bp = "e"
        else:
            ph = EVENT_PHASE_FLOW_BEGIN
            bp = ""
        if details.category_id == PipeDependencyCategory.FLAGS.value:
            cat = "Pipe dependency (flags)"
        elif details.category_id == PipeDependencyCategory.BUFFERS.value:
            cat = "Pipe dependency (buffers)"
        elif details.category_id == PipeDependencyCategory.FFTS.value:
            cat = "Pipe dependency (FFTS Cross Core)"
        else:
            cat = "Pipe dependency (intra blocks)"
        flow_event = FlowEvent(
            details.event_name,
            pid,
            self._resolve_tid(pid, tid),
            ph,
            bp,
            start_tick * self.freq_coeff,
            details.event_id,
            cat,
        )
        return flow_event

    def get_dispatch_flow_event(self, pid: TraceId, tid: str, start_tick: int, details: FlowEventDetails) -> FlowEvent:
        if details.flow_end:
            ph = EVENT_PHASE_FLOW_END
            bp = "e"
        else:
            ph = EVENT_PHASE_FLOW_BEGIN
            bp = ""
        flow_event = FlowEvent(
            details.event_name,
            pid,
            self._resolve_tid(pid, tid),
            ph,
            bp,
            start_tick * self.freq_coeff,
            details.event_id,
            "Dispatch time",
        )
        return flow_event

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        core_type_ids = DatabaseManager.get_cur_core_types(core_id)
        for core_type_id in core_type_ids:
            pipes = DatabaseManager.get_cur_pipes(core_id, core_type_id)
            for pipe in pipes:
                if pipe == InstrType.SCALAR.value:
                    res += self.get_scalar_trace(core_id, core_type_id)
                else:
                    res += self.get_pipe_trace(core_id, core_type_id, pipe)
            simt_instrs = self.get_simt_trace(core_id, core_type_id)

            warp_id_max_len = len(str(max(simt_instrs.keys(), default=0)))
            prefix = self.make_prefix(CoreType(core_type_id), InstrQueueType.SIMT)
            for warp_id, instrs in simt_instrs.items():
                instrs = self.set_inline_instrs_lists(instrs)
                process_name = f"WARP{str(warp_id).zfill(warp_id_max_len)}"
                full_process_name = f"{prefix}{process_name}"
                pid = IBaseTraceBuilder.get_next_process_id(core_id)
                future_events = []
                future_events.append(self.process_name_event(pid, full_process_name))
                future_events += self.get_instr_events(instrs, pid, process_name)
                res += self.events_asdict(future_events)
        return res


class RegionTraceBuilder(IBaseTraceBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.pid_map: Dict[str, TraceId] = dict()
        self.result_trace: List[Any] = list()

    def get_pid(self, pid_name) -> TraceId:
        if pid_name in self.pid_map:
            pid = self.pid_map[pid_name]
        else:
            pid = IBaseTraceBuilder.get_next_process_id(0)
            self.pid_map[pid_name] = pid
            self.result_trace.append(self.process_name_event(pid, pid_name))
        return pid

    def handle_custom_trace(self, core_id: int, custom_regions: List[RegionNode]) -> None:
        for region in custom_regions:
            prefix = self.make_prefix(CoreType(region.core_type), InstrQueueType(region.pipe))
            if region.region_type == InstrQueueType.SIMT.value:
                pid_name = f"{prefix}WARP{str(region.warp_id).zfill(2)}_CUSTOM_TRACE"
            else:
                pipe_name = InstrQueueType(region.pipe).name
                if region.region_type == InstrQueueType.SCALAR.value and pipe_name == "SIMD":
                    pipe_name = "PUSHQ"
                pid_name = f"{prefix}{pipe_name}_CUSTOM_TRACE"
            pid = self.get_pid(pid_name)
            duration_ticks = region.end - region.start
            assert duration_ticks >= 0, "Region with negative duration found"
            ts = region.start * self.freq_coeff
            duration_us = duration_ticks * self.freq_coeff
            meta = self.register_thread(pid, region.name)
            if meta:
                self.result_trace.append(meta)
            trace_event = RegionTracing(
                region.name,
                pid,
                self._resolve_tid(pid, region.name),
                EVENT_PHASE_COMPLETE,
                ts,
                duration_us,
            )
            self.result_trace.append(trace_event)

    def handle_auto_trace(self, core_id: int, auto_regions: List[RegionNode]) -> None:
        for region in auto_regions:
            pid_name = self.make_aux_track_name(CoreType(region.core_type), "AUTO_TRACE", "auto_trace")
            pid = self.get_pid(pid_name)
            duration_ticks = region.end - region.start
            assert duration_ticks >= 0, "Region with negative duration found"
            ts = region.start * self.freq_coeff
            duration_us = duration_ticks * self.freq_coeff
            meta = self.register_thread(pid, region.name)
            if meta:
                self.result_trace.append(meta)
            trace_event = RegionTracing(
                region.name,
                pid,
                self._resolve_tid(pid, region.name),
                EVENT_PHASE_COMPLETE,
                ts,
                duration_us,
            )
            self.result_trace.append(trace_event)

    def get_json_obj(self, core_id: int) -> List[Any]:
        custom_regions, auto_regions = DatabaseManager.get_core_regions(core_id)
        self.handle_custom_trace(core_id, custom_regions)
        self.handle_auto_trace(core_id, auto_regions)
        return IBaseTraceBuilder.events_asdict(self.result_trace)


class QueueEventTraceBuilder(IBaseTraceBuilder):
    def get_isq_full_event(self, pid: TraceId, instr: Any) -> TraceBaseEvent:
        tid_name = instr["InstrTypeName"]
        ts = instr["EventBeginTick"] * self.freq_coeff
        duration_us = (instr["EventEndTick"] - instr["EventBeginTick"]) * self.freq_coeff
        args = QueueFullEventArgs(instr["ExecInstrName"], instr["InstrId"])
        event = StallEvent(
            "QUEUE FULL",
            pid,
            self._resolve_tid(pid, tid_name),
            EVENT_PHASE_COMPLETE,
            ts,
            duration_us,
            CHROME_COLOR_BUILD_FAILED,
            args,
        )
        return event

    def get_trace(self, core_id: int, core_type_id: int) -> List[Any]:
        res: List[Any] = []
        process_name = self.make_aux_track_name(CoreType(core_type_id), "QUEUE_EVENTS", "queue_events")
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        res.append(self.process_name_event(pid, process_name))
        rows = DatabaseManager.get_queue_full_events(core_id, core_type_id)
        for r in rows:
            tid_name = r["InstrTypeName"]
            meta = self.register_thread(pid, tid_name)
            if meta:
                res.append(meta)
            event = self.get_isq_full_event(pid, r)
            res.append(event)
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        core_type_ids = DatabaseManager.get_cur_core_types(core_id)
        for core_type_id in core_type_ids:
            res += self.get_trace(core_id, core_type_id)
        return res


class ICacheTraceBuilder(InstrTraceBuilder):
    def get_icache_refill_event(self, pid: TraceId, tid: int, instr: Any) -> TraceBaseEvent:
        ts = instr["Start"] * self.freq_coeff
        duration_us = (instr["End"] - instr["Start"]) * self.freq_coeff
        addr = hex(instr["SourceInstrAddr"])
        size = instr["CacheLineSize"]
        type = ICacheEventType(instr["ICacheEventType"]).name
        args = CacheRefillEventArgs(addr, size, type)
        event = StallEvent(
            "ICACHE REFILL",
            pid,
            self._resolve_tid(pid, f"ICACHE_SLOT{tid:0>2}"),
            EVENT_PHASE_COMPLETE,
            ts,
            duration_us,
            CHROME_COLOR_BUILD_FAILED,
            args,
        )
        return event

    def get_icache_instr(self, pid: TraceId, instr: Any) -> ICacheReadEvent:
        col = CHROME_COLOR_GOOD if instr["hs"] else CHROME_COLOR_RAIL_ANIMATION
        name = "ICACHE_HIT" if instr["hs"] else "ICACHE_MISS"
        event_type = ICacheEventType(instr["EventType"]).name
        source = f"{instr['Path']}:{instr['LineNum']}"
        args = ICacheEventArgs(
            pid,
            "ICACHE_READ",
            hex(instr["Addr"]),
            source,
            instr["Line"],
            event_type,
            hex(instr["Size"]),
        )
        return ICacheReadEvent(
            name,
            pid,
            self._resolve_tid(pid, "ICACHE_READ"),
            EVENT_PHASE_INSTANT,
            instr["Time"] * self.freq_coeff,
            col,
            EVENT_PHASE_INSTANT_SCOPE_THREAD,
            args,
        )

    def get_trace(self, core_id: int, core_type_id: int) -> List[Any]:
        res: List[Any] = []
        process_name = self.make_aux_track_name(CoreType(core_type_id), "ICACHE_EVENTS", "icache_events")
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        res.append(self.process_name_event(pid, process_name))
        events = DatabaseManager.get_icache_refill_events(core_id, core_type_id)
        events = self.set_inline_instrs_lists(events)
        for tid in range(len(events)):
            res.append(self.thread_name_event(pid, f"ICACHE_SLOT{tid:0>2}"))
            res.extend([self.get_icache_refill_event(pid, tid, i) for i in events[tid]])
        tid = len(events)
        events = DatabaseManager.get_icache_events(core_id, core_type_id)
        if events:
            res.append(self.thread_name_event(pid, "ICACHE_READ"))
        res.extend([self.get_icache_instr(pid, i) for i in events])
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        core_type_ids = DatabaseManager.get_cur_core_types(core_id)
        for core_type_id in core_type_ids:
            res += self.get_trace(core_id, core_type_id)
        return res


class DCacheScalarBiuRequestsTraceBuilder(InstrTraceBuilder):
    def proc_dcache_refill_event(self, pid: TraceId, tid: int, instr: Any) -> TraceBaseEvent:
        ts = instr["Start"] * self.freq_coeff
        duration_us = (instr["End"] - instr["Start"]) * self.freq_coeff
        pc = None if instr["SourceInstrAddr"] is None else hex(instr["SourceInstrAddr"])
        size = instr["Size"]
        addr = hex(instr["Addr"])
        instr_text = instr["InstrText"]
        args = DCacheScalarBiuRequestEventArgs(addr, f"{size}B", pc, instr_text)
        event = DCacheRefillEvent(
            "DCACHE REFILL",
            pid,
            self._resolve_tid(pid, f"DCACHE_SLOT{tid:0>2}"),
            EVENT_PHASE_COMPLETE,
            ts,
            duration_us,
            CHROME_COLOR_BUILD_FAILED,
            args,
        )
        return event

    def proc_dcache_hit_miss_event(self, pid: TraceId, instr: Any) -> TraceBaseEvent:
        ts = instr["Start"] * self.freq_coeff
        pc = hex(instr["SourceInstrAddr"])
        addr = hex(instr["Addr"])
        instr_text = instr["InstrText"]
        is_hit = bool(instr["IsHit"])
        name = "DCACHE_HIT" if is_hit else "DCACHE_MISS"
        color = CHROME_COLOR_GOOD if is_hit else CHROME_COLOR_RAIL_ANIMATION
        args = DCacheScalarHitMissArgs(addr, pc, instr_text)
        event = DCacheHitMissEvent(
            name,
            pid,
            self._resolve_tid(pid, "DCACHE_READ"),
            EVENT_PHASE_INSTANT,
            ts,
            color,
            EVENT_PHASE_INSTANT_SCOPE_THREAD,
            args,
        )
        return event

    def get_trace(self, core_id: int, core_type_id: int) -> List[Any]:
        if not database_handler.table_has_data("DCacheBiuRequests") and not database_handler.table_has_data(
            "DCacheHitMissEvents"
        ):
            return []
        res: List[Any] = []
        process_name = self.make_aux_track_name(CoreType(core_type_id), "DCACHE_EVENTS", "dcache_events")
        pid = IBaseTraceBuilder.get_next_process_id(core_id)
        res.append(self.process_name_event(pid, process_name))
        events = DatabaseManager.get_dcache_refill_events(core_id, core_type_id)
        events = self.set_inline_instrs_lists(events)
        for tid in range(len(events)):
            res.append(self.thread_name_event(pid, f"DCACHE_SLOT{tid:0>2}"))
            res.extend([self.proc_dcache_refill_event(pid, tid, i) for i in events[tid]])
        tid = len(events)
        events = DatabaseManager.get_dcache_hit_miss_events(core_id, core_type_id)
        if events:
            res.append(self.thread_name_event(pid, "DCACHE_READ"))
        res.extend([self.proc_dcache_hit_miss_event(pid, i) for i in events])
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        core_type_ids = DatabaseManager.get_cur_core_types(core_id)
        for core_type_id in core_type_ids:
            res += self.get_trace(core_id, core_type_id)
        return res


class MemoryUtilizationTraceBuilder(IBaseTraceBuilder):
    def __init__(self) -> None:
        super().__init__()
        self.supported_mem_types: List[MemoryType] = [
            MemoryType.AIV0_UB,
            MemoryType.AIV1_UB,
            MemoryType.AIC_L1,
            MemoryType.AIC_L0A,
            MemoryType.AIC_L0B,
            MemoryType.AIC_L0C,
        ]
        self.pid_map: Dict[MemoryType, TraceId] = {}

    def get_trace(self, core_id: int):
        res: List[TraceBaseEvent] = []
        ub_events = DatabaseManager.get_ub_utilization_events(core_id)
        for mem_type in self.supported_mem_types:
            (core_type, memory) = mem_type.name.split("_", 1)
            process_name = self.make_aux_track_name(CoreType[core_type], f"{memory}_UTILIZATION", "memory_utilization")
            pid = IBaseTraceBuilder.get_next_process_id(core_id)
            res.append(self.process_name_event(pid, process_name))
            res.append(self.thread_name_event(pid, "memory_utilization"))
            self.pid_map[mem_type] = pid

        for event in ub_events:
            mem_type = MemoryType(event["MemoryType"])
            tick = int(event["Tick"]) * self.freq_coeff
            buf_size = memory_size_map[biprof_ctx.arch][mem_type]
            pid = self.pid_map[mem_type]
            util_bytes = float(event["UtilizedBytes"])
            util = round((util_bytes / buf_size) * 100, 2)
            args = {"Utilization (%)": util, "Using Bytes (KB)": util_bytes / 1024}
            res.append(
                CountEvent(
                    "memory_utilization",
                    pid,
                    self._resolve_tid(pid, "memory_utilization"),
                    EVENT_PHASE_COUNTER,
                    tick,
                    CHROME_COLOR_RAIL_ANIMATION,
                    args,
                )
            )
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        res += self.get_trace(core_id)
        return res


class UnitUtilizationTraceBuilder(IBaseTraceBuilder):
    def __init__(self):
        super().__init__()
        self.supported_unit_types = [
            CoreType.AIC,
            CoreType.AIV0,
            CoreType.AIV1,
        ]
        self.pid_map = {}

    def get_trace(self, core_id: int):
        res: List[TraceBaseEvent] = []
        util_events = DatabaseManager.get_unit_utilization_events(core_id)
        for unit_type in self.supported_unit_types:
            process_name = self.make_aux_track_name(unit_type, "UNIT_UTILIZATION", "unit_utilization")
            pid = IBaseTraceBuilder.get_next_process_id(core_id)
            res.append(self.process_name_event(pid, process_name))
            res.append(self.thread_name_event(pid, "unit_utilization"))
            self.pid_map[unit_type] = pid
        for event in util_events:
            unit_type = CoreType(event["CoreTypeId"])
            tick = int(event["Tick"]) * self.freq_coeff
            pid = self.pid_map[unit_type]
            util = round(float(event["Util"]) * 100, 2)
            args = {"Utilization (%)": util}
            res.append(
                CountEvent(
                    "unit_utilization",
                    pid,
                    self._resolve_tid(pid, "unit_utilization"),
                    EVENT_PHASE_COUNTER,
                    tick,
                    CHROME_COLOR_RAIL_ANIMATION,
                    args,
                )
            )
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        res += self.get_trace(core_id)
        return res


class SubqueueUtilizationTraceBuilder(IBaseTraceBuilder):
    def __init__(self):
        super().__init__()
        self.supported_pipe_types = [
            CoreType.AIC,
            CoreType.AIV0,
            CoreType.AIV1,
        ]
        self.issue_pid_map = {}
        self.exec_pid_map = {}

    def get_trace(self, core_id: int):
        res: List[TraceBaseEvent] = []
        issque_events = DatabaseManager.get_issue_queue_utilization_events(core_id)
        exec_events = DatabaseManager.get_exec_queue_utilization_events(core_id)
        for core_type in CoreType:
            for pipe in SAMPLING_PIPES_TO_SUBCORES[core_type]:
                group = INSTR_TO_QUEUE_TYPE[InstrType(pipe.value)]
                t_name = f"{pipe.name}_ISSUE_QUEUE_UTILIZATION"
                process_name = self.make_instr_track_name(core_type, group, t_name, CTSectionType.POST)
                pid = IBaseTraceBuilder.get_next_process_id(core_id)
                res.append(self.process_name_event(pid, process_name))
                res.append(self.thread_name_event(pid, "issue_queue_utilization"))
                self.issue_pid_map[core_type, pipe] = pid
                t_name = f"{pipe.name}_EXEC_QUEUE_UTILIZATION"
                process_name = self.make_instr_track_name(core_type, group, t_name, CTSectionType.POST)
                pid = IBaseTraceBuilder.get_next_process_id(core_id)
                res.append(self.process_name_event(pid, process_name))
                res.append(self.thread_name_event(pid, "exec_queue_utilization"))
                self.exec_pid_map[core_type, pipe] = pid
        for event in issque_events:
            core_type = CoreType(event["CoreTypeId"])
            pipe = InstrType(event["InstrTypeId"])
            tick = int(event["Tick"]) * self.freq_coeff
            pid = self.issue_pid_map[core_type, pipe]
            util = event["Util"]
            args = {"Issue Queue Utilization": util}
            res.append(
                CountEvent(
                    "issue_queue_utilization",
                    pid,
                    self._resolve_tid(pid, "issue_queue_utilization"),
                    EVENT_PHASE_COUNTER,
                    tick,
                    CHROME_COLOR_RAIL_ANIMATION,
                    args,
                )
            )
        for event in exec_events:
            core_type = CoreType(event["CoreTypeId"])
            pipe = InstrType(event["InstrTypeId"])
            tick = int(event["Tick"]) * self.freq_coeff
            pid = self.exec_pid_map[core_type, pipe]
            util = event["Util"]
            args = {"Exec Queue Utilization": util}
            res.append(
                CountEvent(
                    "exec_queue_utilization",
                    pid,
                    self._resolve_tid(pid, "exec_queue_utilization"),
                    EVENT_PHASE_COUNTER,
                    tick,
                    CHROME_COLOR_RAIL_ANIMATION,
                    args,
                )
            )
        return self.events_asdict(res)

    def get_json_obj(self, core_id: int) -> List[Any]:
        res = []
        res += self.get_trace(core_id)
        return res


class BubbleTraceBuilder(IBaseTraceBuilder):
    def get_bubble(self, pid, tid, event):
        ts = event["TickStart"] * self.freq_coeff
        dur = (event["TickEnd"] - event["TickStart"]) * self.freq_coeff
        return BubbleEvent(
            "BUBBLE",
            pid,
            tid,
            EVENT_PHASE_COMPLETE,
            ts,
            dur,
            BubbleEventArgs(
                main_pipeline=event["MainPipeline"],
                sub_type=event["SubType"],
                category=event["Category"],
                reason=event["Reason"],
                optimizable=bool(event["Optimizable"]),
                optimization_hint=event["OptimizationHint"],
                waited_pipeline=event["WaitedPipeline"],
                tick_start=event["TickStart"],
                tick_end=event["TickEnd"],
            ),
        )

    def get_trace(self, core_id, core_type_id):
        pid = self.get_next_process_id(core_id)
        prefix = f"{CoreType(core_type_id).name}_"
        tid_name = f"{prefix}BUBBLES"
        res = [
            self.process_name_event(pid, tid_name),
            self.thread_name_event(pid, tid_name),
        ]
        tid = self._resolve_tid(pid, tid_name)
        for event in DatabaseManager.get_bubble_events(core_id, core_type_id):
            res.append(self.get_bubble(pid, tid, event))
        return self.events_asdict(res)

    def get_json_obj(self, core_id):
        res = []
        for core_type_id in DatabaseManager.get_cur_core_types(core_id):
            res += self.get_trace(core_id, core_type_id)
        return res


class ChromeTracingBuilder:
    trace_builder_cls = InstrTraceBuilder

    @classmethod
    def get_json_obj(cls, core_id: int) -> List[Any]:
        res = cls.trace_builder_cls().get_json_obj(core_id)
        res += ICacheTraceBuilder().get_json_obj(core_id)
        res += DCacheScalarBiuRequestsTraceBuilder().get_json_obj(core_id)
        res += QueueEventTraceBuilder().get_json_obj(core_id)
        res += RegionTraceBuilder().get_json_obj(core_id)
        if biprof_ctx.memory_utilization:
            res += MemoryUtilizationTraceBuilder().get_json_obj(core_id)
        res += UnitUtilizationTraceBuilder().get_json_obj(core_id)
        res += SubqueueUtilizationTraceBuilder().get_json_obj(core_id)
        if biprof_ctx.bubble:
            res += BubbleTraceBuilder().get_json_obj(core_id)
        return res

    # Executes in the context of a NEW process!
    @classmethod
    def generate_core_tracing(cls, core: int, tracing_dir: str, name: str) -> int:
        database_handler.reconnect()
        json_obj = cls.get_json_obj(core)
        if not json_obj:  # empty list
            log.critical(f"Failed to generate Chrome {name} on core {core}!")
            return 1
        json_blocks = cls.divide_json(json_obj)
        for i, block in enumerate(json_blocks):
            tracing_file = os.path.join(tracing_dir, f"core_{core}_{name}_report_{i}.json")
            json_dict = {
                "traceEvents": block,
                "displayTimeUnit": "ns",
            }
            json_dict_str = json.dumps(json_dict)
            json_size = len(json_dict_str)
            if json_size > 5242880:  # 5MB
                with gzip.open(tracing_file + ".gz", "wt") as f:
                    f.write(json_dict_str)
            else:
                with open(tracing_file, "w") as f:
                    f.write(json_dict_str)
        extra_traces = [WarpUtilTraceBuilder]
        for trace_cls in extra_traces:
            name = trace_cls.get_name()
            tracing_file = os.path.join(tracing_dir, f"core_{core}_{name}_report.json")
            json_obj = trace_cls.get_json_obj(core)
            if not json_obj:  # empty list
                continue
            json_dict = {
                "traceEvents": json_obj,
                "displayTimeUnit": "ns",
            }
            with open(tracing_file, "w") as f:
                json.dump(json_dict, f)
        return 0

    @classmethod
    def generate(cls, output_dir) -> int:
        core_list = DatabaseManager.get_active_core_list()
        with StageReporter("Chrome Tracing generation"):
            with ProcessPoolExecutor(biprof_ctx.jobs) as executor:
                futures = [
                    executor.submit(cls.generate_core_tracing, core, output_dir, "tracing") for core in core_list
                ]
                for future in as_completed(futures):
                    result = future.result()
                    if result != 0:
                        return 1
        final_logger.add_general_info(f"Chrome Tracing reports: {output_dir}")
        return 0

    @classmethod
    def divide_json(cls, json_obj) -> List[Any]:
        json_limit = int(biprof_ctx.json_max_size)
        cur_len = len(json.dumps(json_obj))
        if cur_len < json_limit or json_limit == -1:
            return [json_obj]
        meta_events = []
        event_blocks = []
        cur_size = 0
        cur_block: List[Any] = []
        for event in json_obj:
            if "ts" not in event:
                meta_events.append(event)
                continue
            event_size = len(json.dumps(event))
            if event_size + cur_size > json_limit:
                event_blocks.append(cur_block)
                cur_block = [event]
                cur_size = event_size
                continue
            cur_block.append(event)
            cur_size += event_size
        event_blocks.append(cur_block)
        res = [meta_events + block for block in event_blocks]
        return res
