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

"""
VF report JSON export from the active BiProf backend database connection.
"""

import json
import logging as log
from itertools import groupby
from pathlib import Path
from typing import Any, Optional, Union


_INSTR_TYPE_TO_PIPE = {
    50: "RVECEX",
    51: "RVECLP",
    52: "RVECSU",
    53: "RVECST",
    54: "RVECLD",
}


def export_vf_report_json(db: Any, output_dir: Union[str, Path], json_stem: str) -> bool:
    """Export VF report JSON from the provided backend DB handle."""
    report = _build_vf_report(db)
    if report is None:
        return False

    json_path = Path(output_dir) / f"{json_stem}_vf_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False)

    log.info("VF report JSON exported: %s", json_path)
    return True


def _build_vf_report(db: Any) -> Optional[dict]:
    """Build VF report data for one kernel launch."""
    rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    tables = {r[0] for r in rows}
    if "ExecutedInstructions" not in tables:
        return None

    has_simd = "VfIPC" in tables and db.execute("SELECT COUNT(*) FROM VfIPC").fetchone()[0] > 0
    has_simt = "VfSimtIPC" in tables and db.execute("SELECT COUNT(*) FROM VfSimtIPC").fetchone()[0] > 0
    if not has_simd and not has_simt:
        return None

    return {
        "version": "1.2",
        "kernel": _extract_kernel_info(db),
        "vfs": _extract_vf_definitions(db, has_simd, has_simt),
        "timeline": _extract_timeline(db),
    }


def _extract_kernel_info(db: Any) -> dict:
    info = {}
    for row in db.execute("SELECT * FROM KernelInfo"):
        info[row["KernelInfoAttr"]] = row["KernelInfoVal"]
    return {
        "name": info.get("name", "unknown"),
        "arch": info.get("arch", "unknown"),
        "frequency_ghz": info.get("frequency_ghz"),
        "active_cores": info.get("ai_core_active"),
        "exec_time_ns": info.get("exec_time_ns"),
        "exec_time_ticks": info.get("exec_time_ticks"),
        "total_instrs_executed": info.get("instrs_exec_num"),
        "scalar_ipc_overall": info.get("scalar_ipc"),
    }


def _extract_vf_definitions(db: Any, has_simd: bool, has_simt: bool) -> list:
    """Group VF definitions by function PC and sort by exec IPC."""
    vfs = []
    if has_simd:
        vfs += _extract_vf_mode(db, "simd")
    if has_simt:
        vfs += _extract_vf_mode(db, "simt")

    vfs.sort(key=lambda v: v["ipc"]["exec"])
    for i, v in enumerate(vfs):
        v["id"] = i

    return vfs


def _extract_vf_mode(db: Any, mode: str) -> list:
    """Extract VF list for one execution mode."""
    if mode == "simd":
        third_key = "scalar"
        sql = """
            SELECT EI.SourceInstrAddr AS PC, EI.ExecInstrCoreId,
                   EI.CoreTypeId, EI.ExecInstrTickStart, EI.ExecInstrTickEnd,
                   VF.ScalarIPC AS ThirdIPC, VF.ExecIPC, VF.LdStIPC
            FROM VfIPC VF
            JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
            ORDER BY EI.ExecInstrTickStart
        """
    else:
        third_key = "branch"
        sql = """
            SELECT EI.SourceInstrAddr AS PC, EI.ExecInstrCoreId,
                   EI.CoreTypeId, EI.ExecInstrTickStart, EI.ExecInstrTickEnd,
                   VF.BranchIPC AS ThirdIPC, VF.ExecIPC, VF.LdStIPC
            FROM VfSimtIPC VF
            JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
            ORDER BY EI.ExecInstrTickStart
        """

    vf_rows = db.execute(sql).fetchall()
    if not vf_rows:
        return []

    invocations = []
    for r in vf_rows:
        func_pc_row = db.execute(
            """
            SELECT MIN(SourceInstrAddr) FROM ExecutedInstructions
            WHERE CoreTypeId = :ctid
              AND InstrTypeId IN (50,51,52,53,54)
              AND ExecInstrTickStart >= :ts AND ExecInstrTickEnd <= :te
        """,
            {
                "ctid": r["CoreTypeId"],
                "ts": r["ExecInstrTickStart"],
                "te": r["ExecInstrTickEnd"],
            },
        ).fetchone()
        func_pc = func_pc_row[0] if func_pc_row and func_pc_row[0] is not None else r["PC"]
        invocations.append(
            {
                "call_site_pc": r["PC"],
                "function_pc": func_pc,
                "core_id": r["ExecInstrCoreId"],
                "core_type_id": r["CoreTypeId"],
                "tick_start": r["ExecInstrTickStart"],
                "tick_end": r["ExecInstrTickEnd"],
                "third_ipc": r["ThirdIPC"],
                "exec_ipc": r["ExecIPC"],
                "ldst_ipc": r["LdStIPC"],
            }
        )

    invocations.sort(key=lambda x: x["function_pc"])
    vfs = []
    for func_pc, group in groupby(invocations, key=lambda x: x["function_pc"]):
        instances = list(group)
        call_sites = sorted(set(f"0x{inv['call_site_pc']:x}" for inv in instances))
        durations = [inv["tick_end"] - inv["tick_start"] for inv in instances]
        avg_dur = sum(durations) / len(durations)
        min_dur = min(durations)
        max_dur = max(durations)

        avg_third = sum(inv["third_ipc"] for inv in instances) / len(instances)
        avg_exec = sum(inv["exec_ipc"] for inv in instances) / len(instances)
        avg_ldst = sum(inv["ldst_ipc"] for inv in instances) / len(instances)
        total_ipc = avg_third + avg_exec + avg_ldst

        longest = max(instances, key=lambda inv: inv["tick_end"] - inv["tick_start"])
        instr_profile = _analyze_vf_instructions(
            db,
            longest["core_id"],
            longest["core_type_id"],
            longest["tick_start"],
            longest["tick_end"],
        )

        vfs.append(
            {
                "id": 0,
                "type": mode,
                "function_pc": f"0x{func_pc:x}",
                "call_sites": call_sites,
                "executions": len(instances),
                "timing": {
                    "avg_cycles": round(avg_dur),
                    "min_cycles": min_dur,
                    "max_cycles": max_dur,
                    "variance_ratio": round(max_dur / max(min_dur, 1), 1),
                },
                "ipc": {
                    third_key: round(avg_third, 3),
                    "exec": round(avg_exec, 3),
                    "ldst": round(avg_ldst, 3),
                    "total": round(total_ipc, 3),
                },
                "instr_profile": instr_profile,
            }
        )

    return vfs


def _analyze_vf_instructions(db: Any, core_id, core_type_id, tick_start, tick_end) -> dict:
    """Analyze VF inner instruction composition."""
    rows = db.execute(
        """
        SELECT ExecInstrName, InstrTypeId, COUNT(*) AS Cnt,
               SUM(ExecInstrTickEnd - ExecInstrTickStart) AS TotalTicks
        FROM ExecutedInstructions
        WHERE ExecInstrCoreId = :cid AND CoreTypeId = :ctid
          AND InstrTypeId IN (50,51,52,53,54)
          AND ExecInstrTickStart >= :ts AND ExecInstrTickEnd <= :te
        GROUP BY ExecInstrName
        ORDER BY Cnt DESC
    """,
        {"cid": core_id, "ctid": core_type_id, "ts": tick_start, "te": tick_end},
    ).fetchall()

    total_count = sum(r["Cnt"] for r in rows)
    by_pipe: dict = {}
    for r in rows:
        pipe = _INSTR_TYPE_TO_PIPE.get(r["InstrTypeId"], "OTHER")
        if pipe not in by_pipe:
            by_pipe[pipe] = {"count": 0, "ticks": 0}
        by_pipe[pipe]["count"] += r["Cnt"]
        by_pipe[pipe]["ticks"] += r["TotalTicks"]

    pipe_list = []
    for pipe, data in sorted(by_pipe.items(), key=lambda x: -x[1]["count"]):
        pipe_list.append(
            {
                "pipe": pipe,
                "count": data["count"],
                "pct": round(data["count"] / max(total_count, 1) * 100, 1),
                "ticks": data["ticks"],
            }
        )

    return {
        "total_count": total_count,
        "by_pipe": pipe_list,
    }


def _extract_timeline(db: Any) -> list:
    """Extract global VF inner-instruction timeline."""
    vf_calls = db.execute("""
        SELECT SourceInstrAddr, CoreTypeId, ExecInstrTickStart, ExecInstrTickEnd
        FROM ExecutedInstructions
        WHERE ExecInstrName = 'VF'
        ORDER BY ExecInstrTickStart
    """).fetchall()

    if not vf_calls:
        return []

    timeline = []
    for vf in vf_calls:
        call_pc = vf["SourceInstrAddr"]
        tick_start = vf["ExecInstrTickStart"]
        tick_end = vf["ExecInstrTickEnd"]
        core_type_id = vf["CoreTypeId"]

        inner_rows = db.execute(
            """
            SELECT SourceInstrAddr, ExecInstrName, InstrTypeId,
                   ExecInstrTickStart, ExecInstrTickEnd
            FROM ExecutedInstructions
            WHERE CoreTypeId = :ctid
              AND InstrTypeId IN (50,51,52,53,54)
              AND ExecInstrTickStart >= :ts AND ExecInstrTickEnd <= :te
            ORDER BY ExecInstrTickStart
        """,
            {"ctid": core_type_id, "ts": tick_start, "te": tick_end},
        ).fetchall()

        if not inner_rows:
            continue

        function_pc = min(r["SourceInstrAddr"] for r in inner_rows)

        for r in inner_rows:
            timeline.append(
                {
                    "tick_start": r["ExecInstrTickStart"],
                    "tick_end": r["ExecInstrTickEnd"],
                    "pc": f"0x{r['SourceInstrAddr']:x}",
                    "name": r["ExecInstrName"],
                    "pipe": _INSTR_TYPE_TO_PIPE.get(r["InstrTypeId"], "OTHER"),
                    "vf_call_pc": f"0x{call_pc:x}",
                    "vf_function_pc": f"0x{function_pc:x}",
                }
            )

    timeline.sort(key=lambda x: (x["tick_start"], x["tick_end"]))
    return timeline
