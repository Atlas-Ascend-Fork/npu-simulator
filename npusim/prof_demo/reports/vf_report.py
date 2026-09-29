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
"""VF report JSON, ported from npusim/prof/src/backend/vf_export.py.

Queries and output layout follow the legacy exporter; legacy quirks (the
timeline only covers ``VF`` calls, function-PC lookup ignores core id) are kept.
"""

from itertools import groupby
from typing import Optional

from npusim.prof_demo.database import database_handler, BackendDBManager
from npusim.prof_demo.enums import InstrType


_INSTR_TYPE_TO_PIPE = {
    50: "RVECEX",
    51: "RVECLP",
    52: "RVECSU",
    53: "RVECST",
    54: "RVECLD",
}
_VECTOR_TYPES = tuple(
    t.value for t in (InstrType.RVECEX, InstrType.RVECLP, InstrType.RVECSU, InstrType.RVECLD, InstrType.RVECST)
)


def scalar_ipc_overall() -> Optional[str]:
    """Legacy scalar_ipc_analyzer: per-subcore non-RVEC count / dispatch span.

    Under the two-table contract DispatchTime is empty, so the legacy
    COALESCE(DispatchTimeTick, ExecInstrTickStart) is ExecInstrTickStart.
    Subcores with a zero or NULL span are skipped as before. If a selected core
    has no usable subcore the legacy analyzer raised ZeroDivisionError, which
    its manager logged and swallowed, leaving the metric absent (JSON null);
    that outcome is returned explicitly here.
    """
    ipc_per_core = []
    for core_id in BackendDBManager.get_active_cores():
        ipc_per_subcore = []
        for core_type_id in BackendDBManager.get_active_core_types():
            count, cycles = database_handler.execute(
                """
                SELECT COUNT(), MAX(ExecInstrTickStart) - MIN(ExecInstrTickStart) FROM ExecutedInstructions
                WHERE ExecInstrCoreId = ? AND CoreTypeId = ? AND InstrTypeId NOT IN {0}
                """.format(_VECTOR_TYPES),
                (core_id, core_type_id),
            ).fetchone()
            if cycles:
                ipc_per_subcore.append(count / cycles)
        if not ipc_per_subcore:
            return None
        ipc_per_core.append(sum(ipc_per_subcore) / len(ipc_per_subcore))
    ipc = round(sum(ipc_per_core) / len(ipc_per_core), 2)
    # Legacy stored it in AnalysisSummary.MetricValue VARCHAR (TEXT affinity).
    return database_handler.execute("SELECT CAST(? AS TEXT)", (ipc,)).fetchone()[0]


def build_vf_report() -> Optional[dict]:
    """Build VF report data for one kernel launch; None without VF IPC rows."""
    has_simd = database_handler.count_rows("VfIPC") > 0
    has_simt = database_handler.count_rows("VfSimtIPC") > 0
    if not has_simd and not has_simt:
        return None
    return {
        "version": "1.2",
        "kernel": _extract_kernel_info(),
        "vfs": _extract_vf_definitions(has_simd, has_simt),
        "timeline": _extract_timeline(),
    }


def _extract_kernel_info() -> dict:
    info = {
        row[0]: row[1] for row in database_handler.execute("SELECT KernelInfoAttr, KernelInfoVal FROM sim.KernelInfo")
    }
    # Legacy UNION ALL let AnalysisSummary.scalar_ipc override KernelInfo; a
    # failed legacy analyzer wrote no row, so KernelInfo (if any) remained.
    scalar_ipc = scalar_ipc_overall()
    if scalar_ipc is not None:
        info["scalar_ipc"] = scalar_ipc
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


def _extract_vf_definitions(has_simd: bool, has_simt: bool) -> list:
    """Group VF definitions by function PC and sort by exec IPC."""
    vfs = []
    if has_simd:
        vfs += _extract_vf_mode("simd")
    if has_simt:
        vfs += _extract_vf_mode("simt")
    vfs.sort(key=lambda v: v["ipc"]["exec"])
    for i, v in enumerate(vfs):
        v["id"] = i
    return vfs


def _extract_vf_mode(mode: str) -> list:
    """Extract VF list for one execution mode."""
    third_key, table, third_column = (
        ("scalar", "VfIPC", "ScalarIPC") if mode == "simd" else ("branch", "VfSimtIPC", "BranchIPC")
    )
    vf_rows = database_handler.execute(
        """
        SELECT EI.SourceInstrAddr AS PC, EI.ExecInstrCoreId,
               EI.CoreTypeId, EI.ExecInstrTickStart, EI.ExecInstrTickEnd,
               VF.{1} AS ThirdIPC, VF.ExecIPC, VF.LdStIPC
        FROM {0} VF
        JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
        ORDER BY EI.ExecInstrTickStart
    """.format(table, third_column)
    ).fetchall()

    invocations = []
    for r in vf_rows:
        func_pc_row = database_handler.execute(
            """
            SELECT MIN(SourceInstrAddr) FROM ExecutedInstructions
            WHERE CoreTypeId = :ctid
              AND InstrTypeId IN (50,51,52,53,54)
              AND ExecInstrTickStart >= :ts AND ExecInstrTickEnd <= :te
        """,
            {"ctid": r["CoreTypeId"], "ts": r["ExecInstrTickStart"], "te": r["ExecInstrTickEnd"]},
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
        min_dur, max_dur = min(durations), max(durations)
        avg_third = sum(inv["third_ipc"] for inv in instances) / len(instances)
        avg_exec = sum(inv["exec_ipc"] for inv in instances) / len(instances)
        avg_ldst = sum(inv["ldst_ipc"] for inv in instances) / len(instances)
        longest = max(instances, key=lambda inv: inv["tick_end"] - inv["tick_start"])
        vfs.append(
            {
                "id": 0,
                "type": mode,
                "function_pc": f"0x{func_pc:x}",
                "call_sites": call_sites,
                "executions": len(instances),
                "timing": {
                    "avg_cycles": round(sum(durations) / len(durations)),
                    "min_cycles": min_dur,
                    "max_cycles": max_dur,
                    "variance_ratio": round(max_dur / max(min_dur, 1), 1),
                },
                "ipc": {
                    third_key: round(avg_third, 3),
                    "exec": round(avg_exec, 3),
                    "ldst": round(avg_ldst, 3),
                    "total": round(avg_third + avg_exec + avg_ldst, 3),
                },
                "instr_profile": _analyze_vf_instructions(
                    longest["core_id"], longest["core_type_id"], longest["tick_start"], longest["tick_end"]
                ),
            }
        )
    return vfs


def _analyze_vf_instructions(core_id, core_type_id, tick_start, tick_end) -> dict:
    """Analyze VF inner instruction composition."""
    rows = database_handler.execute(
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
        data = by_pipe.setdefault(_INSTR_TYPE_TO_PIPE.get(r["InstrTypeId"], "OTHER"), {"count": 0, "ticks": 0})
        data["count"] += r["Cnt"]
        data["ticks"] += r["TotalTicks"]
    pipe_list = [
        {
            "pipe": pipe,
            "count": data["count"],
            "pct": round(data["count"] / max(total_count, 1) * 100, 1),
            "ticks": data["ticks"],
        }
        for pipe, data in sorted(by_pipe.items(), key=lambda x: -x[1]["count"])
    ]
    return {"total_count": total_count, "by_pipe": pipe_list}


def _extract_timeline() -> list:
    """Extract global VF inner-instruction timeline (legacy: 'VF' calls only)."""
    vf_calls = database_handler.execute("""
        SELECT SourceInstrAddr, CoreTypeId, ExecInstrTickStart, ExecInstrTickEnd
        FROM ExecutedInstructions
        WHERE ExecInstrName = 'VF'
        ORDER BY ExecInstrTickStart
    """).fetchall()

    timeline = []
    for vf in vf_calls:
        inner_rows = database_handler.execute(
            """
            SELECT SourceInstrAddr, ExecInstrName, InstrTypeId,
                   ExecInstrTickStart, ExecInstrTickEnd
            FROM ExecutedInstructions
            WHERE CoreTypeId = :ctid
              AND InstrTypeId IN (50,51,52,53,54)
              AND ExecInstrTickStart >= :ts AND ExecInstrTickEnd <= :te
            ORDER BY ExecInstrTickStart
        """,
            {"ctid": vf["CoreTypeId"], "ts": vf["ExecInstrTickStart"], "te": vf["ExecInstrTickEnd"]},
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
                    "vf_call_pc": f"0x{vf['SourceInstrAddr']:x}",
                    "vf_function_pc": f"0x{function_pc:x}",
                }
            )
    timeline.sort(key=lambda x: (x["tick_start"], x["tick_end"]))
    return timeline
