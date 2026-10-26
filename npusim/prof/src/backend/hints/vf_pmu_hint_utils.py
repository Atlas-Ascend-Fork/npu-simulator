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

"""Shared helpers for the VF PMU hints.

The VF PMU hints consume the synthetic top-down counters persisted by the
VfPMUAnalyzer (run with --simd-perf): VfPMUSynthValuesPerSubcore and
VfPMUSynthValuesPerVf, read through the views from analysis.sql. The
hierarchy structure (parent links) is imported from the analyzer's pure
metrics module instead of being re-derived from SQL.

Per-VF hints attribute findings to the VF marker instructions (VF/VFI in
SIMD mode, VF_SIMT in SIMT mode). VfId has no direct join to the
instruction stream, so the Nth marker of a subcore in execution order is
matched to its Nth VfId. The mapping is only trusted when the marker count
equals the subcore's VF count and the VfIds are contiguous; on mismatch
the callers degrade to kernel-level messages.
"""

import logging as log
import sqlite3
from typing import Dict, List, Optional, Tuple

from npusim.prof.src.backend.analyzers.vf_pmu.metrics import METRICS, display_name
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_enum import CoreType

SubcoreKey = Tuple[int, int]
VfKey = Tuple[int, int, int]

# Thresholds, aligned with the VF PMU reporters (vf_pmu_bottleneck_viz,
# vf_pmu_bottleneck_processor, vf_pmu_perf_summary_reporter).
NODE_TRIGGER = 0.085  # hot node: 8.5% of the optimal slots
DEP_TRIGGER = 0.01  # dependency edges (ex_stall_by_ld and friends)
MAJOR_BOUND = 0.30  # dominant L1 bound root
VREG_ADVICE = 0.05  # register pressure advice level
VREG_VF_TRIGGER = 0.30  # per-VF register pressure trigger
UB_BW_TRIGGER = 0.6  # UB bandwidth saturation ratio
WORST_VF_RATIO = 0.5  # retired below 0.5x the subcore weighted average
WORST_VF_MIN_SHARE = 0.01  # skip VFs under 1% of the subcore optimal slots
WORST_VF_TOP_N = 5

# L1 roots that represent a bound; syn_c_retired is the "good" root.
BOUND_ROOTS = (
    "syn_c_frontend_bound",
    "syn_c_backend_asu_bound",
    "syn_c_backend_vec_bound",
)
REG_PRESSURE_NODES = ("syn_c_phy_vreg_stall", "syn_c_phy_preg_stall")
FETCH_NODES = (
    "syn_c_icache_miss",
    "syn_c_vloop_stall",
    "syn_c_idu_fifo_stall",
    "syn_c_idu_sreg_stall",
    "syn_c_idu_instr_stall",
    "syn_c_idu_ports_stall",
    "syn_c_vloop_iteration_change_stall",
    "syn_c_uop_split_stall",
)
MEMBAR_NODES = (
    "syn_c_ld_stall_membar",
    "syn_c_ga_stall_membar",
    "syn_c_st_stall_membar",
    "syn_c_sc_stall_membar",
    "syn_c_asu_mem_barrier",
)
VALU_NODES = ("syn_c_valu_cflt_throughput", "syn_c_valu_cflt_resource")
QUEUE_NODES = ("syn_c_ldq_stall", "syn_c_shq_stall")
GATHER_SCATTER_NODES = (
    "syn_c_ga_stall_src_bound",
    "syn_c_ga_stall_membar",
    "syn_c_ga_stall_fu_cflt",
    "syn_c_ga_stall_others",
    "syn_c_sc_stall_src_bound",
    "syn_c_sc_stall_membar",
    "syn_c_sc_stall_fu_cflt",
    "syn_c_sc_stall_others",
)
DEP_NODES = ("syn_c_ex_stall_by_ld", "syn_c_st_stall_src_bound")
UB_BW_NODE = "syn_c_ub_bw_lsu_ratio"
UB_PORT_NODES = ("syn_c_ub_bw_ldu", "syn_c_ub_bw_stu", "syn_c_ub_bw_gsu")

NODE_ADVICE: Dict[str, str] = {
    "syn_c_frontend_bound": ("frontend bound: the instruction supply limits the vector unit"),
    "syn_c_backend_asu_bound": ("ASU bound: the scalar unit limits the vector unit"),
    "syn_c_backend_vec_bound": ("vector backend bound: stalls limit the vector unit"),
    "syn_c_fetch_latency": ("the frontend takes too long to deliver instructions; see the icache/vloop contributors"),
    "syn_c_icache_miss": ("instruction cache misses: reduce the hot code size or improve the hot loop layout"),
    "syn_c_vloop_stall": ("vector loop overhead: increase the work per iteration (unroll the loop)"),
    "syn_c_fetch_bandwidth": "fetch bandwidth limited: improve code density",
    "syn_c_idu_fifo_stall": "fetch FIFO stalls: improve code density",
    "syn_c_idu_sreg_stall": ("scalar-operand fetch stalls: reduce scalar operand pressure"),
    "syn_c_idu_instr_stall": "instruction fetch stalls: improve code density",
    "syn_c_idu_ports_stall": "dispatch port conflicts: mix instruction classes",
    "syn_c_idu_ports_asu_stall": ("ASU dispatch port conflicts: mix ASU and vector instructions"),
    "syn_c_idu_ports_vec_stall": ("vector dispatch port conflicts: mix ASU and vector instructions"),
    "syn_c_vloop_iteration_change_stall": ("vloop iteration switch overhead: unroll to amortize iteration changes"),
    "syn_c_uop_split_stall": ("instructions split into multiple uops: prefer single-uop forms"),
    "syn_c_asu_load_bound": ("ASU load path saturated: reduce scalar load pressure"),
    "syn_c_asu_store_bound": ("ASU store path saturated: reduce scalar store pressure"),
    "syn_c_asu_mem_barrier": "ASU memory barriers: remove redundant barriers",
    "syn_c_phy_reg_stall": ("physical register file pressure: reduce simultaneously live registers"),
    "syn_c_phy_vreg_stall": ("reduce the number of simultaneously live vector registers and shorten their live ranges"),
    "syn_c_phy_preg_stall": ("reduce the number of simultaneously live scalar registers and shorten their live ranges"),
    "syn_c_issue_que_stall": ("issue queues full: reduce the number of in-flight operations"),
    "syn_c_ldq_stall": "load queue full: reduce the number of in-flight loads",
    "syn_c_shq_stall": ("store queue full: batch stores or reduce in-flight stores"),
    "syn_c_send_stall": ("send-channel backpressure: reduce subcore communication"),
    "syn_c_ld_issue_stall": ("load issue stalls dominate; see the ld/ga contributors"),
    "syn_c_ld_stall_membar": ("loads blocked by memory barriers: remove redundant barriers"),
    "syn_c_ld_stall_fu_cflt": "load FU conflicts: reorder loads",
    "syn_c_ga_stall_src_bound": ("gather operands not ready: compute gather indices earlier"),
    "syn_c_ga_stall_membar": ("gathers blocked by memory barriers: remove redundant barriers"),
    "syn_c_ga_stall_fu_cflt": "gather FU conflicts: reorder gathers",
    "syn_c_ex_issue_stall": ("EX issue stalls dominate; see the ex contributors"),
    "syn_c_ex_stall_src_bound": ("EX source operands not ready: schedule producers earlier"),
    "syn_c_ex_stall_by_ld": ("EX pipelines wait for load results: hoist loads and increase the load-to-use distance"),
    "syn_c_ex_stall_dst_reg_port": ("destination register port conflicts: spread result destinations"),
    "syn_c_ex_stall_valu_cflt": ("VALU conflicts: reorder instructions to avoid same-cycle conflicts"),
    "syn_c_valu_cflt_throughput": (
        "VALU throughput conflicts: reorder instructions to avoid same-cycle dual-issue conflicts"
    ),
    "syn_c_valu_cflt_resource": ("VALU resource conflicts: interleave different functional-unit classes"),
    "syn_c_ex_stall_imbalance": ("EXQ0/EXQ1 imbalance: reorder independent instructions across the two EX queues"),
    "syn_c_st_issue_stall": ("store issue stalls dominate; see the st/sc contributors"),
    "syn_c_st_stall_src_bound": ("stores wait for computed data: compute store operands earlier"),
    "syn_c_st_stall_membar": ("stores blocked by memory barriers: remove redundant barriers"),
    "syn_c_st_stall_fu_cflt": "store FU conflicts: reorder stores",
    "syn_c_sc_stall_src_bound": ("scatter operands not ready: compute scatter data earlier"),
    "syn_c_sc_stall_membar": ("scatters blocked by memory barriers: remove redundant barriers"),
    "syn_c_sc_stall_fu_cflt": "scatter FU conflicts: reorder scatters",
    "syn_c_ub_bw_lsu_ratio": ("UB load/store bandwidth near saturation: widen vector accesses or reduce UB traffic"),
}

# Children of every top-down node, in METRICS order, and the level 2 stall
# candidates (children of the bound roots) used to name a bad VF's dominant
# stall. Derived from the metric definitions so they cannot drift.
_CHILDREN: Dict[str, List[str]] = {}
for _metric in METRICS:
    if _metric.level >= 1 and _metric.parent is not None:
        _CHILDREN.setdefault(_metric.parent, []).append(_metric.name)
STALL_L2_NODES: Tuple[str, ...] = tuple(child for _root in BOUND_ROOTS for child in _CHILDREN.get(_root, []))


def node_advice(node: str) -> str:
    return NODE_ADVICE.get(node, "see the SIMD performance report for details")


def has_vf_pmu_data() -> bool:
    """True when the VfPMUAnalyzer persisted synthetic counters.

    False without --simd-perf or without PMU data in the contract DB; every
    VF PMU hint then passes silently. The tables are created by analysis.sql
    regardless, so only their contents matter (and even they may be missing
    in a degraded DB, hence the OperationalError guard).
    """
    try:
        cursor = database_handler.execute(
            "SELECT 1 FROM VfPMUSynthValuesPerSubcore UNION ALL SELECT 1 FROM VfPMUSynthValuesPerVf LIMIT 1"
        )
        return cursor.fetchone() is not None
    except sqlite3.OperationalError:
        return False


def subcore_label(core_id: int, core_type_id: int) -> str:
    try:
        ct_name = CoreType(core_type_id).name
    except ValueError:
        ct_name = str(core_type_id)
    return f"core {core_id} ({ct_name})"


def fetch_subcore_values() -> Dict[SubcoreKey, Dict[str, float]]:
    """{syn_c_name: value} per subcore, all levels (nodes and ratios)."""
    rows = database_handler.execute(
        "SELECT CoreId, CoreTypeId, SynthName, SynthValue FROM VfPMUSynthViewPerSubcore"
    ).fetchall()
    values: Dict[SubcoreKey, Dict[str, float]] = {}
    for row in rows:
        key = (row["CoreId"], row["CoreTypeId"])
        values.setdefault(key, {})[row["SynthName"]] = float(row["SynthValue"])
    return values


def fetch_vf_values() -> Dict[VfKey, Dict[str, float]]:
    """{syn_c_name: value} per (subcore, VfId), all levels."""
    rows = database_handler.execute(
        "SELECT CoreId, CoreTypeId, VfId, SynthName, SynthValue FROM VfPMUSynthViewPerVf"
    ).fetchall()
    values: Dict[VfKey, Dict[str, float]] = {}
    for row in rows:
        key = (row["CoreId"], row["CoreTypeId"], row["VfId"])
        values.setdefault(key, {})[row["SynthName"]] = float(row["SynthValue"])
    return values


def group_vf_values_by_subcore(
    vf_values: Dict[VfKey, Dict[str, float]],
) -> Dict[SubcoreKey, Dict[int, Dict[str, float]]]:
    result: Dict[SubcoreKey, Dict[int, Dict[str, float]]] = {}
    for (core_id, ct_id, vf_id), nodes in vf_values.items():
        result.setdefault((core_id, ct_id), {})[vf_id] = nodes
    return result


def worst_subcore(values: Dict[SubcoreKey, Dict[str, float]], node: str) -> Optional[Tuple[SubcoreKey, float]]:
    """Subcore with the highest value of one node, or None."""
    worst: Optional[Tuple[SubcoreKey, float]] = None
    for key, nodes in values.items():
        value = nodes.get(node)
        if value is None:
            continue
        if worst is None or value > worst[1]:
            worst = (key, value)
    return worst


def dominant_stall_node(nodes: Dict[str, float]) -> Optional[Tuple[str, float]]:
    """Highest-valued level 2 stall node of one VF (or subcore)."""
    dominant: Optional[Tuple[str, float]] = None
    for node in STALL_L2_NODES:
        value = nodes.get(node)
        if value is None:
            continue
        if dominant is None or value > dominant[1]:
            dominant = (node, value)
    return dominant


def children_of(node: str) -> List[str]:
    return _CHILDREN.get(node, [])


def map_vfids_to_marker_pcs() -> Dict[SubcoreKey, Optional[Dict[int, int]]]:
    """Map each subcore's VfIds to the PCs of the VF marker instructions.

    Returns {subcore: {VfId: PC}} for subcores where the marker count
    matches the VF count and the VfIds are contiguous, {subcore: None}
    for subcores where it does not (attribution disabled there).
    """
    markers: Dict[SubcoreKey, List[int]] = {}
    rows = database_handler.execute(
        "SELECT ExecInstrCoreId AS CoreId, CoreTypeId AS CoreTypeId, "
        "SourceInstrAddr AS PC "
        "FROM ExecutedInstructions "
        "WHERE ExecInstrName IN ('VF', 'VFI', 'VF_SIMT') "
        "ORDER BY ExecInstrCoreId, ExecInstrId"
    ).fetchall()
    for row in rows:
        markers.setdefault((row["CoreId"], row["CoreTypeId"]), []).append(int(row["PC"]))

    # VF counts per subcore: VfPMUSummary is the authoritative one-row-per-VF
    # source; the synthetic values are the fallback when it is missing.
    counts: Dict[SubcoreKey, Tuple[int, int, int]] = {}  # (min, max, count)
    for table in ("VfPMUSummary", "VfPMUSynthValuesPerVf"):
        try:
            count_rows = database_handler.execute(
                f"SELECT CoreId, CoreTypeId, MIN(VfId) AS MinVfId, "
                f"MAX(VfId) AS MaxVfId, COUNT(DISTINCT VfId) AS VfCount "
                f"FROM {table} GROUP BY CoreId, CoreTypeId"
            ).fetchall()
        except sqlite3.OperationalError:
            continue
        for row in count_rows:
            key = (row["CoreId"], row["CoreTypeId"])
            counts.setdefault(
                key,
                (int(row["MinVfId"]), int(row["MaxVfId"]), int(row["VfCount"])),
            )

    mapping: Dict[SubcoreKey, Optional[Dict[int, int]]] = {}
    for key, (min_vfid, max_vfid, vf_count) in counts.items():
        subcore_markers = markers.get(key, [])
        contiguous = max_vfid - min_vfid + 1 == vf_count
        if len(subcore_markers) != vf_count or not contiguous:
            log.debug(
                "VF PMU hints: %d VF markers but %d VFs on %s; per-VF attribution disabled for this subcore",
                len(subcore_markers),
                vf_count,
                subcore_label(*key),
            )
            mapping[key] = None
            continue
        mapping[key] = {min_vfid + i: pc for i, pc in enumerate(subcore_markers)}
    return mapping


def short_name(node: str) -> str:
    """Storage name -> display name (syn_c_retired -> retired)."""
    return display_name(node)
