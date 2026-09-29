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

"""Rebuild helper-equivalent FLAGS and BUFFERS dependency flows."""

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from npusim.prof_demo.database import database_handler


class PipeDependencyCategory(Enum):
    FLAGS = 1
    BUFFERS = 3


SYNC_ACQUIRE_BUFFER = "ACQUIRE_BUFFER"
SYNC_RELEASE_BUFFER = "RELEASE_BUFFER"
SYNC_SET_FLAG = "SET_FLAG"
SYNC_WAIT_FLAG = "WAIT_FLAG"

PIPE_IDS = {
    "SCALAR": 0,
    "VEC": 1,
    "CUBE": 2,
    "MTE1": 3,
    "MTE2": 4,
    "MTE3": 5,
    "FLOWCTRL": 6,
    "FIXP": 7,
}

BUFFER_FLOW_NAMES = {
    "ALL": "ALL pipe awaiting RLS_BUF, pipe is blocked.",
    "SCALAR": "SCALAR pipe awaiting RLS_BUF, pipe is blocked.",
    "VEC": "VEC pipe awaiting RLS_BUF, pipe is blocked.",
    "CUBE": "CUBE pipe awaiting RLS_BUF, pipe is blocked.",
    "MTE1": "MTE1 pipe awaiting RLS_BUF, pipe is blocked.",
    "MTE2": "MTE2 pipe awaiting RLS_BUF, pipe is blocked.",
    "MTE3": "MTE3 pipe awaiting RLS_BUF, pipe is blocked.",
    "FIXP": "FIXP pipe awaiting RLS_BUF, pipe is blocked.",
    "FLOWCTRL": "FLOWCTRL pipe awaiting RLS_BUF, pipe is blocked.",
}
UNKNOWN_BUFFER_FLOW_NAME = "unknown pipe awaiting RLS_BUF, pipe is blocked."

# Rows are the executing pipe (target_pipe); columns are the consumer pipe.
# The consumer is intentionally written first in every fixed disclosure-safe name.
FLAG_FLOW_NAMES = (
    (
        "SCALAR awaiting SCALAR flag, pipe is blocked.",
        "VEC awaiting SCALAR flag, pipe is blocked.",
        "CUBE awaiting SCALAR flag, pipe is blocked.",
        "MTE1 awaiting SCALAR flag, pipe is blocked.",
        "MTE2 awaiting SCALAR flag, pipe is blocked.",
        "MTE3 awaiting SCALAR flag, pipe is blocked.",
        "FLOWCTRL awaiting SCALAR flag, pipe is blocked.",
        "FIXP awaiting SCALAR flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting VEC flag, pipe is blocked.",
        "VEC awaiting VEC flag, pipe is blocked.",
        "CUBE awaiting VEC flag, pipe is blocked.",
        "MTE1 awaiting VEC flag, pipe is blocked.",
        "MTE2 awaiting VEC flag, pipe is blocked.",
        "MTE3 awaiting VEC flag, pipe is blocked.",
        "FLOWCTRL awaiting VEC flag, pipe is blocked.",
        "FIXP awaiting VEC flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting CUBE flag, pipe is blocked.",
        "VEC awaiting CUBE flag, pipe is blocked.",
        "CUBE awaiting CUBE flag, pipe is blocked.",
        "MTE1 awaiting CUBE flag, pipe is blocked.",
        "MTE2 awaiting CUBE flag, pipe is blocked.",
        "MTE3 awaiting CUBE flag, pipe is blocked.",
        "FLOWCTRL awaiting CUBE flag, pipe is blocked.",
        "FIXP awaiting CUBE flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting MTE1 flag, pipe is blocked.",
        "VEC awaiting MTE1 flag, pipe is blocked.",
        "CUBE awaiting MTE1 flag, pipe is blocked.",
        "MTE1 awaiting MTE1 flag, pipe is blocked.",
        "MTE2 awaiting MTE1 flag, pipe is blocked.",
        "MTE3 awaiting MTE1 flag, pipe is blocked.",
        "FLOWCTRL awaiting MTE1 flag, pipe is blocked.",
        "FIXP awaiting MTE1 flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting MTE2 flag, pipe is blocked.",
        "VEC awaiting MTE2 flag, pipe is blocked.",
        "CUBE awaiting MTE2 flag, pipe is blocked.",
        "MTE1 awaiting MTE2 flag, pipe is blocked.",
        "MTE2 awaiting MTE2 flag, pipe is blocked.",
        "MTE3 awaiting MTE2 flag, pipe is blocked.",
        "FLOWCTRL awaiting MTE2 flag, pipe is blocked.",
        "FIXP awaiting MTE2 flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting MTE3 flag, pipe is blocked.",
        "VEC awaiting MTE3 flag, pipe is blocked.",
        "CUBE awaiting MTE3 flag, pipe is blocked.",
        "MTE1 awaiting MTE3 flag, pipe is blocked.",
        "MTE2 awaiting MTE3 flag, pipe is blocked.",
        "MTE3 awaiting MTE3 flag, pipe is blocked.",
        "FLOWCTRL awaiting MTE3 flag, pipe is blocked.",
        "FIXP awaiting MTE3 flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting FLOWCTRL flag, pipe is blocked.",
        "VEC awaiting FLOWCTRL flag, pipe is blocked.",
        "CUBE awaiting FLOWCTRL flag, pipe is blocked.",
        "MTE1 awaiting FLOWCTRL flag, pipe is blocked.",
        "MTE2 awaiting FLOWCTRL flag, pipe is blocked.",
        "MTE3 awaiting FLOWCTRL flag, pipe is blocked.",
        "FLOWCTRL awaiting FLOWCTRL flag, pipe is blocked.",
        "FIXP awaiting FLOWCTRL flag, pipe is blocked.",
    ),
    (
        "SCALAR awaiting FIXP flag, pipe is blocked.",
        "VEC awaiting FIXP flag, pipe is blocked.",
        "CUBE awaiting FIXP flag, pipe is blocked.",
        "MTE1 awaiting FIXP flag, pipe is blocked.",
        "MTE2 awaiting FIXP flag, pipe is blocked.",
        "MTE3 awaiting FIXP flag, pipe is blocked.",
        "FLOWCTRL awaiting FIXP flag, pipe is blocked.",
        "FIXP awaiting FIXP flag, pipe is blocked.",
    ),
)


@dataclass(frozen=True)
class BufferRecord:
    exec_id: int
    tick_start: int
    core_id: int
    core_type_id: int
    sync_kind: str
    target_pipe: Optional[str]
    buf_id: Optional[int]
    buf_mode: Optional[int]
    buf_cnt: Optional[int]


@dataclass(frozen=True)
class FlagRecord:
    exec_id: int
    tick_start: int
    core_id: int
    core_type_id: int
    sync_kind: str
    target_pipe: Optional[str]
    consumer_pipe: Optional[str]
    flag_id: Optional[int]


@dataclass(frozen=True)
class PipeDependencyEdge:
    exec_id: int
    event_id: int
    event_name: str
    flow_end: bool
    category_id: int

    def as_tuple(self) -> Tuple[int, int, str, int, int]:
        return (
            self.exec_id,
            self.event_id,
            self.event_name,
            int(self.flow_end),
            self.category_id,
        )


@dataclass
class FlagFlowState:
    state: int
    event_id: int


def buffer_flow_event_name(target_pipe: Optional[str]) -> str:
    return BUFFER_FLOW_NAMES.get(target_pipe, UNKNOWN_BUFFER_FLOW_NAME)


def flag_flow_event_name(target_pipe: str, consumer_pipe: str) -> str:
    return FLAG_FLOW_NAMES[PIPE_IDS[target_pipe]][PIPE_IDS[consumer_pipe]]


def build_buffer_edges(
    records: Iterable[BufferRecord],
    first_event_id: int = 1,
) -> List[PipeDependencyEdge]:
    ordered = sorted(records, key=lambda record: (record.tick_start, record.exec_id))
    open_flows: Dict[Tuple[int, int, int, int], int] = {}
    edges: List[PipeDependencyEdge] = []
    ordinal = first_event_id - 1

    for record in ordered:
        is_acquire = record.sync_kind == SYNC_ACQUIRE_BUFFER
        is_release = record.sync_kind == SYNC_RELEASE_BUFFER
        if not is_acquire and not is_release:
            continue
        if record.buf_id is None or record.buf_cnt is None or record.buf_mode is None:
            continue
        if is_acquire and (record.buf_mode == 1 or record.buf_cnt < 1):
            continue

        match_cnt = record.buf_cnt - 1 if is_acquire else record.buf_cnt
        key = (record.buf_id, match_cnt, record.core_id, record.core_type_id)
        if key not in open_flows:
            if is_acquire:
                continue
            open_flows[key] = record.exec_id
            continue

        begin_exec_id = open_flows.pop(key)
        ordinal += 1
        event_name = buffer_flow_event_name(record.target_pipe)
        edges.append(
            PipeDependencyEdge(
                begin_exec_id,
                ordinal,
                event_name,
                False,
                PipeDependencyCategory.BUFFERS.value,
            )
        )
        edges.append(
            PipeDependencyEdge(
                record.exec_id,
                ordinal,
                event_name,
                True,
                PipeDependencyCategory.BUFFERS.value,
            )
        )

    return edges


def build_flag_edges(
    records: Iterable[FlagRecord],
    first_event_id: int = 1,
) -> List[PipeDependencyEdge]:
    ordered = sorted(records, key=lambda record: (record.tick_start, record.exec_id))
    flows: Dict[Tuple[str, str, int, int, int], Deque[FlagFlowState]] = {}
    edges: List[PipeDependencyEdge] = []
    ordinal = first_event_id - 1

    for record in ordered:
        if record.sync_kind not in (SYNC_SET_FLAG, SYNC_WAIT_FLAG):
            continue
        if record.flag_id is None or record.target_pipe not in PIPE_IDS or record.consumer_pipe not in PIPE_IDS:
            continue

        target_pipe = str(record.target_pipe)
        consumer_pipe = str(record.consumer_pipe)
        key = (
            target_pipe,
            consumer_pipe,
            record.flag_id,
            record.core_id,
            record.core_type_id,
        )
        queue = flows.setdefault(key, deque())
        state_bit = 0b01 if record.sync_kind == SYNC_SET_FLAG else 0b10
        flow_end = False
        if not queue:
            ordinal += 1
            event_id = ordinal
            queue.appendleft(FlagFlowState(state_bit, event_id))
        else:
            flow = queue[-1]
            flow.state |= state_bit
            if flow.state == 0b11:
                event_id = flow.event_id
                queue.pop()
                flow_end = True
            else:
                ordinal += 1
                event_id = ordinal
                queue.appendleft(FlagFlowState(state_bit, event_id))

        edges.append(
            PipeDependencyEdge(
                record.exec_id,
                event_id,
                flag_flow_event_name(target_pipe, consumer_pipe),
                flow_end,
                PipeDependencyCategory.FLAGS.value,
            )
        )

    return edges


def next_event_id(
    edges: Iterable[PipeDependencyEdge],
    fallback: int = 1,
) -> int:
    max_id = max((edge.event_id for edge in edges), default=0)
    return fallback if max_id == 0 else max_id + 1


class PipeDependencyAnalyzer:
    @staticmethod
    def _load_records() -> Tuple[List[BufferRecord], List[FlagRecord]]:
        rows = database_handler.iter_rows("""
            SELECT EI.ExecInstrId, EI.ExecInstrTickStart,
                   EI.ExecInstrCoreId, EI.CoreTypeId,
                   EP.sync_kind, EP.target_pipe, EP.consumer_pipe,
                   EP.buf_id, EP.buf_mode, EP.buf_cnt, EP.flag_id
            FROM sim.ExecutedInstructions AS EI
            JOIN main.ExtendParamsProjection AS EP
              ON EP.ExecInstrId=EI.ExecInstrId
            WHERE (EP.disclosure_status IS NULL
                   OR EP.disclosure_status = 'SANITIZED')
              AND (
                (EP.buf_id IS NOT NULL AND EP.buf_mode IS NOT NULL
                 AND EP.buf_cnt IS NOT NULL AND EP.sync_kind IS NOT NULL
                 AND EP.target_pipe IS NOT NULL)
                OR
                (EP.flag_id IS NOT NULL AND EP.target_pipe IS NOT NULL
                 AND EP.consumer_pipe IS NOT NULL
                 AND EP.sync_kind IS NOT NULL)
              )
        """)
        buffers: List[BufferRecord] = []
        flags: List[FlagRecord] = []
        for row in rows:
            has_buffer_fields = (
                row["buf_id"] is not None
                and row["buf_mode"] is not None
                and row["buf_cnt"] is not None
                and row["sync_kind"] is not None
                and row["target_pipe"] is not None
            )
            has_flag_fields = (
                row["flag_id"] is not None and row["target_pipe"] is not None and row["consumer_pipe"] is not None
            )
            if has_buffer_fields:
                buffers.append(
                    BufferRecord(
                        int(row["ExecInstrId"]),
                        int(row["ExecInstrTickStart"]),
                        int(row["ExecInstrCoreId"]),
                        int(row["CoreTypeId"]),
                        str(row["sync_kind"]),
                        str(row["target_pipe"]),
                        int(row["buf_id"]),
                        int(row["buf_mode"]),
                        int(row["buf_cnt"]),
                    )
                )
            if has_flag_fields:
                flags.append(
                    FlagRecord(
                        int(row["ExecInstrId"]),
                        int(row["ExecInstrTickStart"]),
                        int(row["ExecInstrCoreId"]),
                        int(row["CoreTypeId"]),
                        str(row["sync_kind"]),
                        str(row["target_pipe"]),
                        str(row["consumer_pipe"]),
                        int(row["flag_id"]),
                    )
                )
        return buffers, flags

    def process(self) -> None:
        buffer_records, flag_records = self._load_records()
        buffer_edges = build_buffer_edges(buffer_records)
        flag_edges = build_flag_edges(flag_records, next_event_id(buffer_edges, 1))
        database_handler.execute("DELETE FROM main.PipeDependency")
        database_handler.executemany(
            """
            INSERT INTO main.PipeDependency(
                ExecInstrId, EventId, EventName, FlowEnd, CategoryId)
            VALUES (?, ?, ?, ?, ?)
        """,
            [edge.as_tuple() for edge in (*buffer_edges, *flag_edges)],
        )
