#!/usr/bin/env python3
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

"""Shared helpers for reading the cannsim_helper record DB."""

import re
import json
import logging
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from npusim.core.report.report_path import find_cannsim_helper_db_files


logger = logging.getLogger(__name__)

# Record DB core type values.
_CORE_TYPE_CUBE = 1
_CORE_TYPE_VECTOR0 = 2
_CORE_TYPE_VECTOR1 = 3
_SYNC_OPS = frozenset(
    {
        "SET_FLAG",
        "SET_FLAGI",
        "WAIT_FLAG",
        "WAIT_FLAGI",
        "HSET_FLAG",
        "HWAIT_FLAG",
        "SET_CROSS_CORE",
        "WAIT_FLAG_DEV",
        "WAIT_FLAG_DEVI",
        "SET_INTRA_BLOCK",
        "SET_INTRA_BLOCKI",
        "WAIT_INTRA_BLOCK",
        "WAIT_INTRA_BLOCKI",
        "BAR",
        "GET_BUF",
        "RLS_BUF",
        "NOP_PIPE",
        "SYNC_ALL",
    }
)


@dataclass(frozen=True)
class RecordInstruction:
    """Instruction fields required by detection rules."""

    instr_id: int
    pc: int
    op: str
    tick: int
    core_id: int
    core_type_id: int
    instr_type_id: int
    is_sync: bool
    flag_id: int
    target_pipe: str
    consumer_pipe: str
    # ExtendParams.queue_type (MTE1/MTE2/MTE3/SIMD/SCALAR/FIXP/CUBE) decides the
    # issue-pipe attribution; file points at the source record DB (evidence location).
    queue_type: str = ""
    file: str = ""


def _core_type_to_ids(core_id: int, core_type_id: int) -> Tuple[int, int]:
    """Map a DB core type to the detector's (core_id, sub_core_id)."""
    if core_type_id == _CORE_TYPE_VECTOR1:
        return core_id, 2
    if core_type_id == _CORE_TYPE_VECTOR0:
        return core_id, 1
    return core_id, 0


def _core_type_to_rw_ids(core_id: int, core_type_id: int) -> Tuple[int, int]:
    """Map a DB core type to the (core_id, sub) space used by rw_events.

    rw_events/issque subs come from the log file name (veccore0 -> 0, veccore1 -> 1,
    cubecore0 -> 0). Use this to align DB-derived events such as the GET_BUF/RLS_BUF
    queue-sync set with that space.
    """
    if core_type_id == _CORE_TYPE_VECTOR1:
        return core_id, 1
    return core_id, 0


_SET_OPS = frozenset(
    {
        "SET_FLAG",
        "SET_FLAGI",
        "HSET_FLAG",
        "SET_CROSS_CORE",
        "SET_INTRA_BLOCK",
        "SET_INTRA_BLOCKI",
    }
)
_WAIT_OPS = frozenset(
    {
        "WAIT_FLAG",
        "WAIT_FLAGI",
        "HWAIT_FLAG",
        "WAIT_FLAG_DEV",
        "WAIT_FLAG_DEVI",
        "WAIT_INTRA_BLOCK",
        "WAIT_INTRA_BLOCKI",
    }
)


def _sync_op_order(op: str) -> int:
    """Same-tick priority: the SET family sorts before the WAIT family.

    ExecInstrId is bit-packed, so ordering by it within one tick does not reflect
    program order. AscendC emits the producer's SetFlag before the consumer's
    WaitFlag; processing SET first lets WaitFlagRule see the set_count already
    arrived.
    """
    if op in _SET_OPS:
        return 0
    if op in _WAIT_OPS:
        return 1
    return 2


def _core_type_to_core_name(core_id: int, core_type_id: int) -> str:
    """Convert DB core coordinates to the core name used by the detectors."""
    _, sub_core_id = _core_type_to_ids(core_id, core_type_id)
    if sub_core_id == 2:
        return f"core{core_id}.vector1"
    if sub_core_id == 1:
        return f"core{core_id}.vector0"
    return f"core{core_id}.cube0"


def _find_record_instr_dbs(input_dir: Path) -> List[Path]:
    """Locate helper DBs produced by the record stage (shares report path rules)."""
    return find_cannsim_helper_db_files(Path(input_dir))


# Allow-list for sqlite identifiers, so table/column names cannot be injected into
# PRAGMA or string-built queries.
_DB_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def has_record_db_table_with_column(input_dir: Path, table: str, column: str) -> bool:
    """True if any record DB has the given table and column (old schemas give False)."""
    if not _DB_IDENTIFIER_RE.match(table) or not _DB_IDENTIFIER_RE.match(column):
        return False
    for db_path in _find_record_instr_dbs(input_dir):
        try:
            con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
        except sqlite3.Error:
            continue
        try:
            has_table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if has_table is None:
                continue
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if column in columns:
                return True
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return False


def _record_sync_semantics(raw: Optional[str]) -> Tuple[int, str, str]:
    """Extract sync semantics from ExtendParams JSON, falling back to conservative values."""
    try:
        params = json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid ExtendParams JSON") from exc
    if not isinstance(params, dict):
        raise ValueError("ExtendParams is not an object")

    flag_id = params.get("sync_id", params.get("flag_id", params.get("ffts_flag_id")))
    try:
        flag_id = int(flag_id) if flag_id is not None else -1
    except (TypeError, ValueError):
        flag_id = -1
    target_pipe = str(params.get("target_pipe") or "?").upper()
    consumer_pipe = str(params.get("consumer_pipe") or "?").upper()
    return flag_id, target_pipe, consumer_pipe


def collect_record_instructions(
    input_path: str,
    ops: Optional[frozenset] = None,
) -> List[RecordInstruction]:
    """Read instructions from the ``ExecutedInstructions`` table of the helper record DB."""
    instructions: List[RecordInstruction] = []
    for db_path in _find_record_instr_dbs(Path(input_path)):
        instructions.extend(_load_record_instructions(db_path, ops))
    instructions.sort(key=lambda item: (item.tick, _sync_op_order(item.op), item.instr_id))
    return instructions


def collect_record_sync_events(
    input_path: str,
    ops: Optional[frozenset] = None,
) -> List[RecordInstruction]:
    """Read the sync instructions from the helper record DB."""
    return [record for record in collect_record_instructions(input_path, ops) if record.is_sync]


def _record_query(ops: Optional[frozenset]) -> Tuple[str, tuple]:
    """Build the query selecting only the columns an instruction record needs."""
    where = ""
    query_params: tuple = ()
    if ops is not None:
        placeholders = ",".join("?" for _ in ops)
        where = f"WHERE ExecInstrName IN ({placeholders})"
        query_params = tuple(ops)
    query = f"""
        SELECT ExecInstrId, ExecInstrCoreId, CoreTypeId, ExecInstrName,
               ExecInstrTickStart, InstrTypeId, SourceInstrAddr, ExtendParams
        FROM ExecutedInstructions
        {where}
        ORDER BY ExecInstrCoreId, CoreTypeId, ExecInstrTickStart,
                 ExecInstrId
    """
    return query, query_params


def _parse_record_instruction(
    row: tuple,
    ops: Optional[frozenset],
    db_path: Path,
    start_pc: int = 0,
) -> Optional[RecordInstruction]:
    """Convert one instruction row into a RecordInstruction; None on bad ExtendParams."""
    exec_instr_id, core_id, core_type_id, op, tick, instr_type_id, source_pc, raw_params = row
    if ops is not None and op not in ops:
        return None
    try:
        extend_params = json.loads(raw_params) if raw_params else {}
        if not isinstance(extend_params, dict):
            raise ValueError("ExtendParams is not an object")
        flag_id, target_pipe, consumer_pipe = _record_sync_semantics(raw_params)
        # queue_type is the issue-queue name in ExtendParams.
        queue_type = str(extend_params.get("queue_type") or "").upper()
        # is_sync is true if the DB flags it, the op is a known sync instruction, or
        # sync_kind is present. The DB writes "is_sync":0 for BAR (it runs on the FC
        # queue rather than being a sync of its own), so an explicit 0 must not
        # override the instruction-name classification.
        is_sync = bool(extend_params.get("is_sync")) or op in _SYNC_OPS or bool(extend_params.get("sync_kind"))
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid ExtendParams on ExecInstrId=%s in %s: %s", exec_instr_id, db_path, exc)
        return None
    try:
        return RecordInstruction(
            instr_id=int(exec_instr_id) if exec_instr_id is not None else 0,
            pc=(int(source_pc) + start_pc) if source_pc is not None else 0,
            op=str(op),
            tick=int(tick) if tick is not None else 0,
            core_id=int(core_id) if core_id is not None else 0,
            core_type_id=int(core_type_id) if core_type_id is not None else 0,
            instr_type_id=int(instr_type_id) if instr_type_id is not None else 0,
            is_sync=is_sync,
            flag_id=flag_id,
            target_pipe=target_pipe,
            consumer_pipe=consumer_pipe,
            queue_type=queue_type,
            file=str(db_path),
        )
    except (TypeError, ValueError) as exc:
        logger.warning("Invalid instruction row ExecInstrId=%s in %s: %s", exec_instr_id, db_path, exc)
        return None


def _load_record_instructions(db_path: Path, ops: Optional[frozenset]) -> List[RecordInstruction]:
    """Load instruction records from one read-only record DB."""
    query, query_params = _record_query(ops)
    try:
        con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("Failed to open record DB %s: %s", db_path, exc)
        return []
    try:
        # SourceInstrAddr is a kernel-base offset; absolute PC = start_pc + SourceInstrAddr.
        start_pc = 0
        try:
            row = con.execute("SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'start_pc'").fetchone()
            if row is not None:
                start_pc = int(row[0])
        except (sqlite3.Error, TypeError, ValueError):
            start_pc = 0
        rows = con.execute(query, query_params)
        records = []
        for row in rows:
            record = _parse_record_instruction(row, ops, db_path, start_pc)
            if record is not None:
                records.append(record)
        return records
    except sqlite3.Error as exc:
        logger.warning("Failed to read record DB %s: %s", db_path, exc)
        return []
    finally:
        con.close()


def _load_device_pcs_from_record_db(
    record_db: Path,
) -> List[Tuple[str, int]]:
    """Read de-duplicated (core_name, device_pc) pairs from the helper DB."""
    from npusim.core.report.detections.pc_source_mapper import is_device_pc

    # Select only the columns PC collection needs, so the query survives unrelated
    # helper DB schema evolution.
    query = (
        "SELECT DISTINCT ExecInstrCoreId, CoreTypeId, SourceInstrAddr "
        "FROM ExecutedInstructions "
        "WHERE SourceInstrAddr IS NOT NULL "
        "ORDER BY ExecInstrCoreId, CoreTypeId, SourceInstrAddr"
    )
    uri = f"{record_db.resolve().as_uri()}?mode=ro"
    result: List[Tuple[str, int]] = []
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as e:
        logger.warning("Failed to open instruction DB %s: %s", record_db, e)
        return result

    try:
        rows = con.execute(query)
        for core_id, core_type_id, pc in rows:
            try:
                core_id = int(core_id)
                core_type_id = int(core_type_id)
                pc = int(pc)
            except (TypeError, ValueError):
                continue
            if not is_device_pc(pc):
                continue
            result.append((_core_type_to_core_name(core_id, core_type_id), pc))
    except sqlite3.Error as e:
        logger.warning("Failed to read instruction DB %s: %s", record_db, e)
    finally:
        con.close()
    return result


def collect_device_pcs_from_record_db(input_dir: Path) -> Dict[str, List[int]]:
    """Collect the unique device PCs needed for source mapping from the helper record DB."""
    order_by_core: Dict[str, List[int]] = {}
    seen: Set[Tuple[str, int]] = set()
    for record_db in _find_record_instr_dbs(input_dir):
        for core_name, pc in _load_device_pcs_from_record_db(record_db):
            key = (core_name, pc)
            if key in seen:
                continue
            seen.add(key)
            order_by_core.setdefault(core_name, []).append(pc)
    return order_by_core
