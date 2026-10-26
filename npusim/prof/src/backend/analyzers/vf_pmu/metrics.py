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

"""Synthetic VF PMU counters: definitions and evaluation.

Pure module: no database, no logging, no I/O. Everything here is a function
of a dict of raw PMU *delta* counters.

The same METRICS list serves two scopes: subcore (fed a subcore's cumulative
counters) and per-VF (fed one VF's deltas). Both go through the same pure
evaluate(); a metric's `scope` says which scope(s) persist it. Evaluation
itself is always total - a per-VF metric may depend on a subcore-only
intermediate - so everything is computed and filtered afterwards.
"""

from dataclasses import dataclass
from enum import Flag, auto
from typing import Callable, Dict, List, Optional, Set, Tuple

IDU_WIDTH = 6
ISU_MAX_EX = 2
ISU_MAX_LD = 2
ISU_MAX_ST = 1
ISU_MAX_GA = 1
UB_PEAK_LSU = 512.0


# ---------------------------------------------------------------------------
# Evaluation model
#
# Every entry is a synthetic counter `syn_c_<name>`, derived from raw PMU
# delta counters and/or earlier synthetics. level 0 = plain intermediate
# (no parent); level 1..N = a top-down hierarchy node (parent is None only
# at a root). `expr` is the formula in human-readable form, written with the
# same names as `fn`, so the two can't drift apart.
#
# Naming: c_* = raw PMU counter from the DB. syn_c_* = anything derived.
# ---------------------------------------------------------------------------


class MetricScope(Flag):
    """Where a metric's value is persisted. Bitmask; mirrors SynthScope."""

    SUBCORE = auto()
    VF = auto()
    BOTH = SUBCORE | VF


class Ctx:
    """Namespace holding raw counters + everything derived so far.

    Missing keys read as 0.0 (raw PMU counters may be absent for a given VF).
    Every read is recorded, so we can report exactly which raw counters the
    formulas consumed - and which of them were absent and silently defaulted.
    """

    def __init__(self, raw: Dict[str, float]) -> None:
        self._d: Dict[str, float] = dict(raw)
        self._present: Set[str] = set(raw)  # keys that came from the DB
        self.reads: Set[str] = set()  # keys read by any formula

    def __getitem__(self, key: str) -> float:
        self.reads.add(key)
        return self._d.get(key, 0.0)

    def __setitem__(self, key: str, value: float) -> None:
        self._d[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._d

    def is_present(self, key: str) -> bool:
        return key in self._present


@dataclass(frozen=True)
class Metric:
    name: str
    expr: str  # human-readable formula (names as in fn)
    fn: Callable[[Ctx], float]
    level: int = 0  # 0 = plain counter, >=1 = top-down node
    parent: Optional[str] = None  # only meaningful when level >= 1
    # None -> defaulted by level: counters are subcore-only, hierarchy is both.
    scope: Optional[MetricScope] = None
    # Evaluated after the top-down hierarchy, so its fn may read level >= 1
    # values; stays level 0 itself, so it's excluded from every topdown view.
    custom: bool = False


@dataclass(frozen=True)
class RawUsage:
    value: float
    present: bool  # False -> counter absent in the DB, defaulted to 0.0


def _ratio(num: float, den: float) -> float:
    """num / den, or 0.0 when the denominator is non-positive."""
    return num / den if den > 0 else 0.0


def _split(num: float, den: float, parent: float) -> float:
    """Proportional split: (num / den) x parent, or 0.0 when den <= 0.

    This is the single pattern every top-down child follows: distribute a
    parent metric across siblings by their raw counts.
    """
    return num / den * parent if den > 0 else 0.0


def scope_of(metric: Metric) -> MetricScope:
    """Resolved persistence scope.

    Default: level 0 counters are intermediates, useful for tracing a subcore
    but not worth one row per VF per counter. Hierarchy nodes are stored in
    both scopes. Override per metric with `scope=`.
    """
    if metric.scope is not None:
        return metric.scope
    return MetricScope.SUBCORE if metric.level == 0 else MetricScope.BOTH


# ---------------------------------------------------------------------------
# All synthetic counters, in dependency order: a metric may reference any
# metric defined above it. Levels 0 first, then the top-down hierarchy.
#
#   x = multiply (proportional scale by the parent metric)
# ---------------------------------------------------------------------------
METRICS: List[Metric] = [
    # =====================================================================
    # Level 0: intermediate counts
    # =====================================================================
    # syn_c_optimal is the denominator of every level 1 node, so it is worth
    # keeping per VF as well: it tells you how long the VF actually ran.
    Metric(
        "syn_c_optimal",
        "c_cycle_rvec_busy x IDU_WIDTH",
        lambda c: c["c_cycle_rvec_busy"] * IDU_WIDTH,
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_cycle_rvec_busy_adj",
        "c_cycle_rvec_busy - c_cycle_icache_miss_exclude",
        lambda c: c["c_cycle_rvec_busy"] - c["c_cycle_icache_miss_exclude"],
    ),
    # frontend / backend slot split
    Metric(
        "syn_c_slot_frontend_bound",
        "max(0, c_slot_idu_not_disp.fe_bound - c_cycle_send_stall x IDU_WIDTH)",
        lambda c: max(0.0, c["c_slot_idu_not_disp.fe_bound"] - c["c_cycle_send_stall"] * IDU_WIDTH),
    ),
    Metric(
        "syn_c_slot_frontend_bound_dlt",
        "c_slot_idu_not_disp.fe_bound - syn_c_slot_frontend_bound",
        lambda c: c["c_slot_idu_not_disp.fe_bound"] - c["syn_c_slot_frontend_bound"],
    ),
    Metric(
        "syn_c_slot_backend_vec_bound",
        "c_slot_idu_not_disp.vec_stall + syn_c_slot_frontend_bound_dlt",
        lambda c: c["c_slot_idu_not_disp.vec_stall"] + c["syn_c_slot_frontend_bound_dlt"],
    ),
    # fetch latency
    Metric(
        "syn_c_idu_fifo_empty_adj",
        "max(0, c_cycle_idu_fifo_empty - c_cycle_icache_miss_exclude)",
        lambda c: max(0.0, c["c_cycle_idu_fifo_empty"] - c["c_cycle_icache_miss_exclude"]),
    ),
    Metric(
        "syn_c_cycle_fetch_latency",
        "max(0, syn_c_idu_fifo_empty_adj - c_cycle_send_stall)",
        lambda c: max(0.0, c["syn_c_idu_fifo_empty_adj"] - c["c_cycle_send_stall"]),
    ),
    # EX aggregate stall
    Metric(
        "syn_c_exq_shq_src_stall",
        "c_cycle_shq_avail_exq0_empty + c_cycle_shq_avail_exq1_empty",
        lambda c: c["c_cycle_shq_avail_exq0_empty"] + c["c_cycle_shq_avail_exq1_empty"],
    ),
    Metric(
        "syn_c_exq_imbalance",
        "c_exq0_has_ge2_exq1_has_eq0 + c_exq1_has_ge2_exq0_has_eq0",
        lambda c: c["c_exq0_has_ge2_exq1_has_eq0"] + c["c_exq1_has_ge2_exq0_has_eq0"],
    ),
    Metric(
        "syn_c_ex_stall",
        "c_exq0_issue_eq0 + c_exq1_issue_eq0 + syn_c_exq_imbalance + syn_c_exq_shq_src_stall",
        lambda c: c["c_exq0_issue_eq0"]
        + c["c_exq1_issue_eq0"]
        + c["syn_c_exq_imbalance"]
        + c["syn_c_exq_shq_src_stall"],
    ),
    # LD aggregate stall (ld + ga)
    Metric(
        "syn_c_ld_ld_issue",
        "c_ldq_ld_issue_eq0 x 2 + c_ldq_ld_issue_eq1",
        lambda c: c["c_ldq_ld_issue_eq0"] * 2 + c["c_ldq_ld_issue_eq1"],
    ),
    Metric(
        "syn_c_ld_ld_fu_cflt_base",
        "c_ldq_ld_stall.fu_cflt x 2",
        lambda c: c["c_ldq_ld_stall.fu_cflt"] * 2,
    ),
    Metric(
        "syn_c_ld_ld_fu_cflt_delta",
        "max(0, c_stat_ldu_blocked - syn_c_ld_ld_fu_cflt_base)",
        lambda c: max(0.0, c["c_stat_ldu_blocked"] - c["syn_c_ld_ld_fu_cflt_base"]),
    ),
    Metric(
        "syn_c_ld_ld_stall",
        "syn_c_ld_ld_issue + syn_c_ld_ld_fu_cflt_delta",
        lambda c: c["syn_c_ld_ld_issue"] + c["syn_c_ld_ld_fu_cflt_delta"],
    ),
    Metric(
        "syn_c_ld_ga_stall",
        "c_stq_ga_issue_eq0 + c_shq_ga_issue_eq0",
        lambda c: c["c_stq_ga_issue_eq0"] + c["c_shq_ga_issue_eq0"],
    ),
    Metric(
        "syn_c_ld_stall",
        "syn_c_ld_ld_stall + syn_c_ld_ga_stall",
        lambda c: c["syn_c_ld_ld_stall"] + c["syn_c_ld_ga_stall"],
    ),
    # ST aggregate stall (st + sc)
    Metric(
        "syn_c_st_st_stall",
        "c_shq_st_issue_eq0 + c_stq_st_issue_eq0",
        lambda c: c["c_shq_st_issue_eq0"] + c["c_stq_st_issue_eq0"],
    ),
    Metric(
        "syn_c_st_sc_stall",
        "c_shq_sc_issue_eq0 + c_stq_sc_issue_eq0",
        lambda c: c["c_shq_sc_issue_eq0"] + c["c_stq_sc_issue_eq0"],
    ),
    Metric(
        "syn_c_st_stall",
        "syn_c_st_st_stall + syn_c_st_sc_stall",
        lambda c: c["syn_c_st_st_stall"] + c["syn_c_st_sc_stall"],
    ),
    # hardware-dimension denominators
    Metric(
        "syn_c_sum_phy_reg_stall",
        "c_cycle_phy_reg_stall",
        lambda c: c["c_cycle_phy_reg_stall"],
    ),
    Metric(
        "syn_c_sum_que_full_stall",
        "c_cycle_shq_stall + c_cycle_ldq_stall + c_cycle_stq_stall + c_cycle_exq0_stall + c_cycle_exq1_stall",
        lambda c: c["c_cycle_shq_stall"]
        + c["c_cycle_ldq_stall"]
        + c["c_cycle_stq_stall"]
        + c["c_cycle_exq0_stall"]
        + c["c_cycle_exq1_stall"],
    ),
    Metric(
        "syn_c_the_sum_hw",
        "syn_c_sum_phy_reg_stall + syn_c_sum_que_full_stall + c_cycle_send_stall",
        lambda c: c["syn_c_sum_phy_reg_stall"] + c["syn_c_sum_que_full_stall"] + c["c_cycle_send_stall"],
    ),
    Metric(
        "syn_c_the_sum_instr",
        "syn_c_ex_stall + syn_c_ld_stall + syn_c_st_stall",
        lambda c: c["syn_c_ex_stall"] + c["syn_c_ld_stall"] + c["syn_c_st_stall"],
    ),
    # EX detail counts
    Metric(
        "syn_c_ex_src_bound",
        "c_cycle_exq0_src_not_ready_all + c_cycle_exq1_src_not_ready_all + syn_c_exq_shq_src_stall",
        lambda c: c["c_cycle_exq0_src_not_ready_all"]
        + c["c_cycle_exq1_src_not_ready_all"]
        + c["syn_c_exq_shq_src_stall"],
    ),
    Metric(
        "syn_c_ex_dst_reg_port",
        "c_cycle_exq0_dst_regport_cflt + c_cycle_exq1_dst_regport_cflt",
        lambda c: c["c_cycle_exq0_dst_regport_cflt"] + c["c_cycle_exq1_dst_regport_cflt"],
    ),
    Metric(
        "syn_c_ex_valu_cflt",
        "c_cycle_exq0_valu_cflt + c_cycle_exq1_valu_cflt",
        lambda c: c["c_cycle_exq0_valu_cflt"] + c["c_cycle_exq1_valu_cflt"],
    ),
    Metric(
        "syn_c_ex_others",
        "syn_c_ex_stall - syn_c_ex_src_bound - syn_c_ex_dst_reg_port - syn_c_ex_valu_cflt - syn_c_exq_imbalance",
        lambda c: c["syn_c_ex_stall"]
        - c["syn_c_ex_src_bound"]
        - c["syn_c_ex_dst_reg_port"]
        - c["syn_c_ex_valu_cflt"]
        - c["syn_c_exq_imbalance"],
    ),
    Metric(
        "syn_c_valu_cflt_thrput",
        "c_cycle_exq0_valu_cflt_thrput + c_cycle_exq1_valu_cflt_thrput",
        lambda c: c["c_cycle_exq0_valu_cflt_thrput"] + c["c_cycle_exq1_valu_cflt_thrput"],
    ),
    Metric(
        "syn_c_valu_cflt_resc",
        "c_cycle_exq0_valu_cflt_resc + c_cycle_exq1_valu_cflt_resc",
        lambda c: c["c_cycle_exq0_valu_cflt_resc"] + c["c_cycle_exq1_valu_cflt_resc"],
    ),
    # Count of src-bound cycles attributable to LD. Named _src_bound_by_ld,
    # not _stall_by_ld: the top-down ratio below owns the _stall_by_ld name.
    Metric(
        "syn_c_ex_src_bound_by_ld",
        "min(c_cycle_shq_ex_src_bound_by_ld, syn_c_ex_src_bound)",
        lambda c: min(c["c_cycle_shq_ex_src_bound_by_ld"], c["syn_c_ex_src_bound"]),
    ),
    # LD/GA detail counts
    Metric(
        "syn_c_ld_ld_membar",
        "c_ldq_ld_stall.membar x 2",
        lambda c: c["c_ldq_ld_stall.membar"] * 2,
    ),
    Metric(
        "syn_c_ld_ld_fu_cflt_final",
        "syn_c_ld_ld_fu_cflt_base + syn_c_ld_ld_fu_cflt_delta",
        lambda c: c["syn_c_ld_ld_fu_cflt_base"] + c["syn_c_ld_ld_fu_cflt_delta"],
    ),
    Metric(
        "syn_c_ld_ld_others",
        "max(0, syn_c_ld_ld_stall - syn_c_ld_ld_membar - syn_c_ld_ld_fu_cflt_final)",
        lambda c: max(
            0.0,
            c["syn_c_ld_ld_stall"] - c["syn_c_ld_ld_membar"] - c["syn_c_ld_ld_fu_cflt_final"],
        ),
    ),
    Metric(
        "syn_c_ld_ga_src_bound",
        "c_cycle_shq_ga_src_not_ready_all + c_cycle_stq_ga_src_not_ready_all",
        lambda c: c["c_cycle_shq_ga_src_not_ready_all"] + c["c_cycle_stq_ga_src_not_ready_all"],
    ),
    Metric(
        "syn_c_ld_ga_membar",
        "c_shq_ga_stall.membar + c_stq_ga_stall.membar",
        lambda c: c["c_shq_ga_stall.membar"] + c["c_stq_ga_stall.membar"],
    ),
    Metric(
        "syn_c_ld_ga_fu_cflt",
        "c_shq_ga_stall.fu_cflt + c_stq_ga_stall.fu_cflt",
        lambda c: c["c_shq_ga_stall.fu_cflt"] + c["c_stq_ga_stall.fu_cflt"],
    ),
    Metric(
        "syn_c_ld_ga_others",
        "max(0, syn_c_ld_ga_stall - syn_c_ld_ga_src_bound - syn_c_ld_ga_membar - syn_c_ld_ga_fu_cflt)",
        lambda c: max(
            0.0,
            c["syn_c_ld_ga_stall"] - c["syn_c_ld_ga_src_bound"] - c["syn_c_ld_ga_membar"] - c["syn_c_ld_ga_fu_cflt"],
        ),
    ),
    # denominators previously inline inside the top-down computation
    Metric(
        "syn_c_sum_idu_stall",
        "c_cycle_idu_fifo_stall + c_cycle_idu_sreg_stall + c_cycle_idu_instr_stall + c_cycle_idu_ports_stall",
        lambda c: c["c_cycle_idu_fifo_stall"]
        + c["c_cycle_idu_sreg_stall"]
        + c["c_cycle_idu_instr_stall"]
        + c["c_cycle_idu_ports_stall"],
    ),
    Metric(
        "syn_c_sum_idu_ports",
        "c_cycle_idu_ports_stall.asu + c_cycle_idu_ports_stall.vec",
        lambda c: c["c_cycle_idu_ports_stall.asu"] + c["c_cycle_idu_ports_stall.vec"],
    ),
    Metric(
        "syn_c_icache_miss_adj",
        "c_cycle_icache_miss - c_cycle_icache_miss_exclude",
        lambda c: c["c_cycle_icache_miss"] - c["c_cycle_icache_miss_exclude"],
    ),
    Metric(
        "syn_c_sum_icache_vloop",
        "syn_c_icache_miss_adj + c_cycle_vloop_stall",
        lambda c: c["syn_c_icache_miss_adj"] + c["c_cycle_vloop_stall"],
    ),
    Metric(
        "syn_c_sum_phy_preg_vreg",
        "c_cycle_phy_preg_stall + c_cycle_phy_vreg_stall",
        lambda c: c["c_cycle_phy_preg_stall"] + c["c_cycle_phy_vreg_stall"],
    ),
    Metric(
        "syn_c_sum_valu_cflt",
        "syn_c_valu_cflt_thrput + syn_c_valu_cflt_resc",
        lambda c: c["syn_c_valu_cflt_thrput"] + c["syn_c_valu_cflt_resc"],
    ),
    # ST/SC detail counts
    Metric(
        "syn_c_st_st_src",
        "c_cycle_shq_st_src_not_ready_all + c_cycle_stq_st_src_not_ready_all",
        lambda c: c["c_cycle_shq_st_src_not_ready_all"] + c["c_cycle_stq_st_src_not_ready_all"],
    ),
    Metric(
        "syn_c_st_st_membar",
        "c_shq_st_stall.membar + c_stq_st_stall.membar",
        lambda c: c["c_shq_st_stall.membar"] + c["c_stq_st_stall.membar"],
    ),
    Metric(
        "syn_c_st_st_fu",
        "c_shq_st_stall.fu_cflt + c_stq_st_stall.fu_cflt",
        lambda c: c["c_shq_st_stall.fu_cflt"] + c["c_stq_st_stall.fu_cflt"],
    ),
    Metric(
        "syn_c_st_st_others",
        "max(0, syn_c_st_st_stall - syn_c_st_st_src - syn_c_st_st_membar - syn_c_st_st_fu)",
        lambda c: max(
            0.0,
            c["syn_c_st_st_stall"] - c["syn_c_st_st_src"] - c["syn_c_st_st_membar"] - c["syn_c_st_st_fu"],
        ),
    ),
    Metric(
        "syn_c_st_sc_src",
        "c_cycle_shq_sc_src_not_ready_all + c_cycle_stq_sc_src_not_ready_all",
        lambda c: c["c_cycle_shq_sc_src_not_ready_all"] + c["c_cycle_stq_sc_src_not_ready_all"],
    ),
    Metric(
        "syn_c_st_sc_membar",
        "c_shq_sc_stall.membar + c_stq_sc_stall.membar",
        lambda c: c["c_shq_sc_stall.membar"] + c["c_stq_sc_stall.membar"],
    ),
    Metric(
        "syn_c_st_sc_fu",
        "c_shq_sc_stall.fu_cflt + c_stq_sc_stall.fu_cflt",
        lambda c: c["c_shq_sc_stall.fu_cflt"] + c["c_stq_sc_stall.fu_cflt"],
    ),
    Metric(
        "syn_c_st_sc_others",
        "max(0, syn_c_st_sc_stall - syn_c_st_sc_src - syn_c_st_sc_membar - syn_c_st_sc_fu)",
        lambda c: max(
            0.0,
            c["syn_c_st_sc_stall"] - c["syn_c_st_sc_src"] - c["syn_c_st_sc_membar"] - c["syn_c_st_sc_fu"],
        ),
    ),
    # -------------------------------------------------------------------
    # Plain-counter ratios consumed by the per-VF report. Level 0 (they are
    # not hierarchy nodes), but scope=BOTH so the reporter reads them per VF
    # instead of recomputing anything. _ratio() yields 0.0 on a zero
    # denominator: an idle VF has zero deltas everywhere.
    # -------------------------------------------------------------------
    # IPC by instruction class, over the VF's vector busy cycles.
    # GA (gather, rvecld_ga) and SC (scatter, rvecst_sc) are counted
    # separately - see PMU.txt metric_star ga_back_ipc / sc_back_ipc, which
    # are two distinct metrics, not one combined bucket.
    Metric(
        "syn_c_ipc_su",
        "c_slot_idu_disp_rvecsu / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecsu"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_ex",
        "c_slot_idu_disp_rvecex / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecex"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_ld",
        "c_slot_idu_disp_rvecld_ld / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ld"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_st",
        "c_slot_idu_disp_rvecst_st / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecst_st"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_lp",
        "c_slot_idu_disp_rveclp / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rveclp"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_ga",
        "c_slot_idu_disp_rvecld_ga / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ga"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_sc",
        "c_slot_idu_disp_rvecst_sc / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecst_sc"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_total",
        "c_slot_idu_disp_total / c_cycle_rvec_busy",
        lambda c: _ratio(c["c_slot_idu_disp_total"], c["c_cycle_rvec_busy"]),
        scope=MetricScope.BOTH,
    ),
    # Effective UB bandwidth, bytes per busy cycle of each port.
    Metric(
        "syn_c_ub_grant_lsu",
        "c_stat_ldu_grant_size + c_stat_stu_grant_size",
        lambda c: c["c_stat_ldu_grant_size"] + c["c_stat_stu_grant_size"],
    ),
    Metric(
        "syn_c_ub_bw_ldu",
        "c_stat_ldu_grant_size / c_stat_ub_ldu_busy",
        lambda c: _ratio(c["c_stat_ldu_grant_size"], c["c_stat_ub_ldu_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ub_bw_stu",
        "c_stat_stu_grant_size / c_stat_ub_stu_busy",
        lambda c: _ratio(c["c_stat_stu_grant_size"], c["c_stat_ub_stu_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ub_bw_lsu",
        "syn_c_ub_grant_lsu / c_stat_ub_lsu_busy",
        lambda c: _ratio(c["syn_c_ub_grant_lsu"], c["c_stat_ub_lsu_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ub_bw_gsu",
        "c_stat_gsu_grant_size / c_stat_ub_gsu_busy",
        lambda c: _ratio(c["c_stat_gsu_grant_size"], c["c_stat_ub_gsu_busy"]),
        scope=MetricScope.BOTH,
    ),
    # Queue and register file occupancy: used slots over busy cycles.
    Metric(
        "syn_c_util_vreg",
        "c_stat_vreg_used / c_stat_vreg_busy",
        lambda c: _ratio(c["c_stat_vreg_used"], c["c_stat_vreg_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_util_preg",
        "c_stat_preg_used / c_stat_preg_busy",
        lambda c: _ratio(c["c_stat_preg_used"], c["c_stat_preg_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_util_ldq",
        "c_stat_ldq_used / c_stat_ldq_busy",
        lambda c: _ratio(c["c_stat_ldq_used"], c["c_stat_ldq_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_util_shq",
        "c_stat_shq_used / c_stat_shq_busy",
        lambda c: _ratio(c["c_stat_shq_used"], c["c_stat_shq_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_util_exq0",
        "c_stat_exq0_used / c_stat_exq0_busy",
        lambda c: _ratio(c["c_stat_exq0_used"], c["c_stat_exq0_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_util_exq1",
        "c_stat_exq1_used / c_stat_exq1_busy",
        lambda c: _ratio(c["c_stat_exq1_used"], c["c_stat_exq1_busy"]),
        scope=MetricScope.BOTH,
    ),
    # IPC over back-end busy cycles + efficiency vs the ISU peak. Different
    # denominator from syn_c_ipc_* above (c_cycle_rvec_busy) despite the name.
    Metric(
        "syn_c_ipc_back_ex",
        "c_slot_idu_disp_rvecex / c_cycle_rvec_back_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecex"], c["c_cycle_rvec_back_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_ld",
        "c_slot_idu_disp_rvecld_ld / c_cycle_rvec_back_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ld"], c["c_cycle_rvec_back_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_st",
        "c_slot_idu_disp_rvecst_st / c_cycle_rvec_back_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecst_st"], c["c_cycle_rvec_back_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_ga",
        "c_slot_idu_disp_rvecld_ga / c_cycle_rvec_back_busy",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ga"], c["c_cycle_rvec_back_busy"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_ex_ratio",
        "syn_c_ipc_back_ex / ISU_MAX_EX",
        lambda c: _ratio(c["syn_c_ipc_back_ex"], ISU_MAX_EX),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_ld_ratio",
        "syn_c_ipc_back_ld / ISU_MAX_LD",
        lambda c: _ratio(c["syn_c_ipc_back_ld"], ISU_MAX_LD),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_st_ratio",
        "syn_c_ipc_back_st / ISU_MAX_ST",
        lambda c: _ratio(c["syn_c_ipc_back_st"], ISU_MAX_ST),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ipc_back_ga_ratio",
        "syn_c_ipc_back_ga / ISU_MAX_GA",
        lambda c: _ratio(c["syn_c_ipc_back_ga"], ISU_MAX_GA),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ub_bw_lsu_ratio",
        "syn_c_ub_bw_lsu / UB_PEAK_LSU",
        lambda c: _ratio(c["syn_c_ub_bw_lsu"], UB_PEAK_LSU),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_ex",
        "c_slot_idu_disp_rvecex / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecex"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_ld",
        "c_slot_idu_disp_rvecld_ld / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ld"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_st",
        "c_slot_idu_disp_rvecst_st / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecst_st"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_ga",
        "c_slot_idu_disp_rvecld_ga / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecld_ga"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_sc",
        "c_slot_idu_disp_rvecst_sc / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecst_sc"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_su",
        "c_slot_idu_disp_rvecsu / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rvecsu"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    Metric(
        "syn_c_ratio_lp",
        "c_slot_idu_disp_rveclp / c_slot_idu_disp_total",
        lambda c: _ratio(c["c_slot_idu_disp_rveclp"], c["c_slot_idu_disp_total"]),
        scope=MetricScope.BOTH,
    ),
    # =====================================================================
    # Level 1: the four top-down roots, all normalized by syn_c_optimal
    # =====================================================================
    Metric(
        "syn_c_retired",
        "c_slot_idu_disp_total / syn_c_optimal",
        lambda c: _ratio(c["c_slot_idu_disp_total"], c["syn_c_optimal"]),
        level=1,
    ),
    Metric(
        "syn_c_frontend_bound",
        "syn_c_slot_frontend_bound / syn_c_optimal",
        lambda c: _ratio(c["syn_c_slot_frontend_bound"], c["syn_c_optimal"]),
        level=1,
    ),
    Metric(
        "syn_c_backend_asu_bound",
        "c_slot_idu_not_disp.asu_busy / syn_c_optimal",
        lambda c: _ratio(c["c_slot_idu_not_disp.asu_busy"], c["syn_c_optimal"]),
        level=1,
    ),
    Metric(
        "syn_c_backend_vec_bound",
        "syn_c_slot_backend_vec_bound / syn_c_optimal",
        lambda c: _ratio(c["syn_c_slot_backend_vec_bound"], c["syn_c_optimal"]),
        level=1,
    ),
    # Percentage (0-100) siblings of the four L1 roots above, reading them
    # directly rather than re-deriving from raw - see Metric.custom.
    Metric(
        "syn_c_retired_pct",
        "syn_c_retired x 100",
        lambda c: c["syn_c_retired"] * 100,
        scope=MetricScope.BOTH,
        custom=True,
    ),
    Metric(
        "syn_c_frontend_bound_pct",
        "syn_c_frontend_bound x 100",
        lambda c: c["syn_c_frontend_bound"] * 100,
        scope=MetricScope.BOTH,
        custom=True,
    ),
    Metric(
        "syn_c_backend_asu_bound_pct",
        "syn_c_backend_asu_bound x 100",
        lambda c: c["syn_c_backend_asu_bound"] * 100,
        scope=MetricScope.BOTH,
        custom=True,
    ),
    Metric(
        "syn_c_backend_vec_bound_pct",
        "syn_c_backend_vec_bound x 100",
        lambda c: c["syn_c_backend_vec_bound"] * 100,
        scope=MetricScope.BOTH,
        custom=True,
    ),
    # --- syn_c_retired -> per instruction class
    Metric(
        "syn_c_retired_rvecex",
        "c_slot_idu_disp_rvecex / c_slot_idu_disp_total x syn_c_retired",
        lambda c: _split(c["c_slot_idu_disp_rvecex"], c["c_slot_idu_disp_total"], c["syn_c_retired"]),
        level=2,
        parent="syn_c_retired",
    ),
    Metric(
        "syn_c_retired_rvecld",
        "c_slot_idu_disp_rvecld / c_slot_idu_disp_total x syn_c_retired",
        lambda c: _split(c["c_slot_idu_disp_rvecld"], c["c_slot_idu_disp_total"], c["syn_c_retired"]),
        level=2,
        parent="syn_c_retired",
    ),
    Metric(
        "syn_c_retired_rvecst",
        "c_slot_idu_disp_rvecst / c_slot_idu_disp_total x syn_c_retired",
        lambda c: _split(c["c_slot_idu_disp_rvecst"], c["c_slot_idu_disp_total"], c["syn_c_retired"]),
        level=2,
        parent="syn_c_retired",
    ),
    Metric(
        "syn_c_retired_rvecsu",
        "c_slot_idu_disp_rvecsu / c_slot_idu_disp_total x syn_c_retired",
        lambda c: _split(c["c_slot_idu_disp_rvecsu"], c["c_slot_idu_disp_total"], c["syn_c_retired"]),
        level=2,
        parent="syn_c_retired",
    ),
    Metric(
        "syn_c_retired_rveclp",
        "c_slot_idu_disp_rveclp / c_slot_idu_disp_total x syn_c_retired",
        lambda c: _split(c["c_slot_idu_disp_rveclp"], c["c_slot_idu_disp_total"], c["syn_c_retired"]),
        level=2,
        parent="syn_c_retired",
    ),
    # --- syn_c_frontend_bound
    Metric(
        "syn_c_fetch_latency",
        "syn_c_cycle_fetch_latency / syn_c_cycle_rvec_busy_adj  (independent ratio, NOT scaled by parent)",
        lambda c: _ratio(c["syn_c_cycle_fetch_latency"], c["syn_c_cycle_rvec_busy_adj"]),
        level=2,
        parent="syn_c_frontend_bound",
    ),
    Metric(
        "syn_c_icache_miss",
        "syn_c_icache_miss_adj / syn_c_sum_icache_vloop x syn_c_fetch_latency",
        lambda c: _split(
            c["syn_c_icache_miss_adj"],
            c["syn_c_sum_icache_vloop"],
            c["syn_c_fetch_latency"],
        ),
        level=3,
        parent="syn_c_fetch_latency",
    ),
    Metric(
        "syn_c_vloop_stall",
        "c_cycle_vloop_stall / syn_c_sum_icache_vloop x syn_c_fetch_latency",
        lambda c: _split(
            c["c_cycle_vloop_stall"],
            c["syn_c_sum_icache_vloop"],
            c["syn_c_fetch_latency"],
        ),
        level=3,
        parent="syn_c_fetch_latency",
    ),
    Metric(
        "syn_c_fetch_bandwidth",
        "syn_c_frontend_bound - syn_c_fetch_latency  (remainder, may be < 0)",
        lambda c: c["syn_c_frontend_bound"] - c["syn_c_fetch_latency"],
        level=2,
        parent="syn_c_frontend_bound",
    ),
    Metric(
        "syn_c_idu_fifo_stall",
        "c_cycle_idu_fifo_stall / syn_c_sum_idu_stall x syn_c_fetch_bandwidth",
        lambda c: _split(
            c["c_cycle_idu_fifo_stall"],
            c["syn_c_sum_idu_stall"],
            c["syn_c_fetch_bandwidth"],
        ),
        level=3,
        parent="syn_c_fetch_bandwidth",
    ),
    Metric(
        "syn_c_idu_sreg_stall",
        "c_cycle_idu_sreg_stall / syn_c_sum_idu_stall x syn_c_fetch_bandwidth",
        lambda c: _split(
            c["c_cycle_idu_sreg_stall"],
            c["syn_c_sum_idu_stall"],
            c["syn_c_fetch_bandwidth"],
        ),
        level=3,
        parent="syn_c_fetch_bandwidth",
    ),
    Metric(
        "syn_c_idu_instr_stall",
        "c_cycle_idu_instr_stall / syn_c_sum_idu_stall x syn_c_fetch_bandwidth",
        lambda c: _split(
            c["c_cycle_idu_instr_stall"],
            c["syn_c_sum_idu_stall"],
            c["syn_c_fetch_bandwidth"],
        ),
        level=3,
        parent="syn_c_fetch_bandwidth",
    ),
    Metric(
        "syn_c_vloop_iteration_change_stall",
        "c_cycle_idu_instr_stall.vloop_iter / c_cycle_idu_instr_stall x syn_c_idu_instr_stall",
        lambda c: _split(
            c["c_cycle_idu_instr_stall.vloop_iter"],
            c["c_cycle_idu_instr_stall"],
            c["syn_c_idu_instr_stall"],
        ),
        level=4,
        parent="syn_c_idu_instr_stall",
    ),
    Metric(
        "syn_c_uop_split_stall",
        "c_cycle_idu_instr_stall.uop_split / c_cycle_idu_instr_stall x syn_c_idu_instr_stall",
        lambda c: _split(
            c["c_cycle_idu_instr_stall.uop_split"],
            c["c_cycle_idu_instr_stall"],
            c["syn_c_idu_instr_stall"],
        ),
        level=4,
        parent="syn_c_idu_instr_stall",
    ),
    Metric(
        "syn_c_areg_about_stall",
        "syn_c_idu_instr_stall - syn_c_vloop_iteration_change_stall - syn_c_uop_split_stall  (remainder, clamped >= 0)",
        lambda c: max(
            0.0,
            c["syn_c_idu_instr_stall"] - c["syn_c_vloop_iteration_change_stall"] - c["syn_c_uop_split_stall"],
        ),
        level=4,
        parent="syn_c_idu_instr_stall",
    ),
    Metric(
        "syn_c_idu_ports_stall",
        "c_cycle_idu_ports_stall / syn_c_sum_idu_stall x syn_c_fetch_bandwidth",
        lambda c: _split(
            c["c_cycle_idu_ports_stall"],
            c["syn_c_sum_idu_stall"],
            c["syn_c_fetch_bandwidth"],
        ),
        level=3,
        parent="syn_c_fetch_bandwidth",
    ),
    Metric(
        "syn_c_idu_ports_asu_stall",
        "c_cycle_idu_ports_stall.asu / syn_c_sum_idu_ports x syn_c_idu_ports_stall",
        lambda c: _split(
            c["c_cycle_idu_ports_stall.asu"],
            c["syn_c_sum_idu_ports"],
            c["syn_c_idu_ports_stall"],
        ),
        level=4,
        parent="syn_c_idu_ports_stall",
    ),
    Metric(
        "syn_c_idu_ports_vec_stall",
        "c_cycle_idu_ports_stall.vec / syn_c_sum_idu_ports x syn_c_idu_ports_stall",
        lambda c: _split(
            c["c_cycle_idu_ports_stall.vec"],
            c["syn_c_sum_idu_ports"],
            c["syn_c_idu_ports_stall"],
        ),
        level=4,
        parent="syn_c_idu_ports_stall",
    ),
    # --- syn_c_backend_asu_bound
    Metric(
        "syn_c_asu_load_bound",
        "c_slot_idu_not_disp.asu_ld_busy / syn_c_optimal",
        lambda c: _ratio(c["c_slot_idu_not_disp.asu_ld_busy"], c["syn_c_optimal"]),
        level=2,
        parent="syn_c_backend_asu_bound",
    ),
    Metric(
        "syn_c_asu_store_bound",
        "c_slot_idu_not_disp.asu_st_busy / syn_c_optimal",
        lambda c: _ratio(c["c_slot_idu_not_disp.asu_st_busy"], c["syn_c_optimal"]),
        level=2,
        parent="syn_c_backend_asu_bound",
    ),
    Metric(
        "syn_c_asu_mem_barrier",
        "c_slot_idu_not_disp.asu_mem_bar / syn_c_optimal",
        lambda c: _ratio(c["c_slot_idu_not_disp.asu_mem_bar"], c["syn_c_optimal"]),
        level=2,
        parent="syn_c_backend_asu_bound",
    ),
    # --- syn_c_backend_vec_bound: HARDWARE cut (den. syn_c_the_sum_hw)
    Metric(
        "syn_c_phy_reg_stall",
        "syn_c_sum_phy_reg_stall / syn_c_the_sum_hw x syn_c_backend_vec_bound",
        lambda c: _split(
            c["syn_c_sum_phy_reg_stall"],
            c["syn_c_the_sum_hw"],
            c["syn_c_backend_vec_bound"],
        ),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    Metric(
        "syn_c_phy_preg_stall",
        "c_cycle_phy_preg_stall / syn_c_sum_phy_preg_vreg x syn_c_phy_reg_stall",
        lambda c: _split(
            c["c_cycle_phy_preg_stall"],
            c["syn_c_sum_phy_preg_vreg"],
            c["syn_c_phy_reg_stall"],
        ),
        level=3,
        parent="syn_c_phy_reg_stall",
    ),
    Metric(
        "syn_c_phy_vreg_stall",
        "c_cycle_phy_vreg_stall / syn_c_sum_phy_preg_vreg x syn_c_phy_reg_stall",
        lambda c: _split(
            c["c_cycle_phy_vreg_stall"],
            c["syn_c_sum_phy_preg_vreg"],
            c["syn_c_phy_reg_stall"],
        ),
        level=3,
        parent="syn_c_phy_reg_stall",
    ),
    Metric(
        "syn_c_issue_que_stall",
        "syn_c_sum_que_full_stall / syn_c_the_sum_hw x syn_c_backend_vec_bound",
        lambda c: _split(
            c["syn_c_sum_que_full_stall"],
            c["syn_c_the_sum_hw"],
            c["syn_c_backend_vec_bound"],
        ),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    Metric(
        "syn_c_ldq_stall",
        "c_cycle_ldq_stall / syn_c_sum_que_full_stall x syn_c_issue_que_stall  (stq/exq* children are NOT emitted)",
        lambda c: _split(
            c["c_cycle_ldq_stall"],
            c["syn_c_sum_que_full_stall"],
            c["syn_c_issue_que_stall"],
        ),
        level=3,
        parent="syn_c_issue_que_stall",
    ),
    Metric(
        "syn_c_shq_stall",
        "c_cycle_shq_stall / syn_c_sum_que_full_stall x syn_c_issue_que_stall",
        lambda c: _split(
            c["c_cycle_shq_stall"],
            c["syn_c_sum_que_full_stall"],
            c["syn_c_issue_que_stall"],
        ),
        level=3,
        parent="syn_c_issue_que_stall",
    ),
    Metric(
        "syn_c_send_stall",
        "c_cycle_send_stall / syn_c_the_sum_hw x syn_c_backend_vec_bound",
        lambda c: _split(c["c_cycle_send_stall"], c["syn_c_the_sum_hw"], c["syn_c_backend_vec_bound"]),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    # --- syn_c_backend_vec_bound: INSTRUCTION cut (syn_c_the_sum_instr).
    #     Second, orthogonal decomposition of the SAME parent: the hw children
    #     above and the instruction children below each sum to it separately.
    Metric(
        "syn_c_ld_issue_stall",
        "syn_c_ld_stall / syn_c_the_sum_instr x syn_c_backend_vec_bound",
        lambda c: _split(c["syn_c_ld_stall"], c["syn_c_the_sum_instr"], c["syn_c_backend_vec_bound"]),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    Metric(
        "syn_c_ld_stall_membar",
        "syn_c_ld_ld_membar / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ld_membar"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ld_stall_fu_cflt",
        "syn_c_ld_ld_fu_cflt_final / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(
            c["syn_c_ld_ld_fu_cflt_final"],
            c["syn_c_ld_stall"],
            c["syn_c_ld_issue_stall"],
        ),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ld_stall_others",
        "syn_c_ld_ld_others / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ld_others"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ga_stall_src_bound",
        "syn_c_ld_ga_src_bound / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ga_src_bound"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ga_stall_membar",
        "syn_c_ld_ga_membar / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ga_membar"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ga_stall_fu_cflt",
        "syn_c_ld_ga_fu_cflt / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ga_fu_cflt"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ga_stall_others",
        "syn_c_ld_ga_others / syn_c_ld_stall x syn_c_ld_issue_stall",
        lambda c: _split(c["syn_c_ld_ga_others"], c["syn_c_ld_stall"], c["syn_c_ld_issue_stall"]),
        level=3,
        parent="syn_c_ld_issue_stall",
    ),
    Metric(
        "syn_c_ex_issue_stall",
        "syn_c_ex_stall / syn_c_the_sum_instr x syn_c_backend_vec_bound",
        lambda c: _split(c["syn_c_ex_stall"], c["syn_c_the_sum_instr"], c["syn_c_backend_vec_bound"]),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    Metric(
        "syn_c_ex_stall_src_bound",
        "syn_c_ex_src_bound / syn_c_ex_stall x syn_c_ex_issue_stall",
        lambda c: _split(c["syn_c_ex_src_bound"], c["syn_c_ex_stall"], c["syn_c_ex_issue_stall"]),
        level=3,
        parent="syn_c_ex_issue_stall",
    ),
    Metric(
        "syn_c_ex_stall_by_ld",
        "syn_c_ex_src_bound_by_ld / syn_c_ex_src_bound x syn_c_ex_stall_src_bound",
        lambda c: _split(
            c["syn_c_ex_src_bound_by_ld"],
            c["syn_c_ex_src_bound"],
            c["syn_c_ex_stall_src_bound"],
        ),
        level=4,
        parent="syn_c_ex_stall_src_bound",
    ),
    Metric(
        "syn_c_ex_stall_dst_reg_port",
        "syn_c_ex_dst_reg_port / syn_c_ex_stall x syn_c_ex_issue_stall",
        lambda c: _split(c["syn_c_ex_dst_reg_port"], c["syn_c_ex_stall"], c["syn_c_ex_issue_stall"]),
        level=3,
        parent="syn_c_ex_issue_stall",
    ),
    Metric(
        "syn_c_ex_stall_valu_cflt",
        "syn_c_ex_valu_cflt / syn_c_ex_stall x syn_c_ex_issue_stall",
        lambda c: _split(c["syn_c_ex_valu_cflt"], c["syn_c_ex_stall"], c["syn_c_ex_issue_stall"]),
        level=3,
        parent="syn_c_ex_issue_stall",
    ),
    Metric(
        "syn_c_valu_cflt_throughput",
        "syn_c_valu_cflt_thrput / syn_c_sum_valu_cflt x syn_c_ex_stall_valu_cflt",
        lambda c: _split(
            c["syn_c_valu_cflt_thrput"],
            c["syn_c_sum_valu_cflt"],
            c["syn_c_ex_stall_valu_cflt"],
        ),
        level=4,
        parent="syn_c_ex_stall_valu_cflt",
    ),
    Metric(
        "syn_c_valu_cflt_resource",
        "syn_c_valu_cflt_resc / syn_c_sum_valu_cflt x syn_c_ex_stall_valu_cflt",
        lambda c: _split(
            c["syn_c_valu_cflt_resc"],
            c["syn_c_sum_valu_cflt"],
            c["syn_c_ex_stall_valu_cflt"],
        ),
        level=4,
        parent="syn_c_ex_stall_valu_cflt",
    ),
    Metric(
        "syn_c_ex_stall_imbalance",
        "syn_c_exq_imbalance / syn_c_ex_stall x syn_c_ex_issue_stall",
        lambda c: _split(c["syn_c_exq_imbalance"], c["syn_c_ex_stall"], c["syn_c_ex_issue_stall"]),
        level=3,
        parent="syn_c_ex_issue_stall",
    ),
    Metric(
        "syn_c_ex_stall_others",
        "syn_c_ex_others / syn_c_ex_stall x syn_c_ex_issue_stall  (NOT clamped, exact remainder, may be < 0)",
        lambda c: _split(c["syn_c_ex_others"], c["syn_c_ex_stall"], c["syn_c_ex_issue_stall"]),
        level=3,
        parent="syn_c_ex_issue_stall",
    ),
    Metric(
        "syn_c_st_issue_stall",
        "syn_c_st_stall / syn_c_the_sum_instr x syn_c_backend_vec_bound",
        lambda c: _split(c["syn_c_st_stall"], c["syn_c_the_sum_instr"], c["syn_c_backend_vec_bound"]),
        level=2,
        parent="syn_c_backend_vec_bound",
    ),
    Metric(
        "syn_c_st_stall_src_bound",
        "syn_c_st_st_src / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_st_src"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_st_stall_membar",
        "syn_c_st_st_membar / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_st_membar"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_st_stall_fu_cflt",
        "syn_c_st_st_fu / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_st_fu"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_st_stall_others",
        "syn_c_st_st_others / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_st_others"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_sc_stall_src_bound",
        "syn_c_st_sc_src / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_sc_src"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_sc_stall_membar",
        "syn_c_st_sc_membar / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_sc_membar"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_sc_stall_fu_cflt",
        "syn_c_st_sc_fu / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_sc_fu"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
    Metric(
        "syn_c_sc_stall_others",
        "syn_c_st_sc_others / syn_c_st_stall x syn_c_st_issue_stall",
        lambda c: _split(c["syn_c_st_sc_others"], c["syn_c_st_stall"], c["syn_c_st_issue_stall"]),
        level=3,
        parent="syn_c_st_issue_stall",
    ),
]


# Storage names carry the syn_c_ prefix; consumers (reporters) display the
# short form. Both live here so nothing hardcodes the prefix elsewhere.
SYN_PREFIX = "syn_c_"


def display_name(name: str) -> str:
    """Storage name -> display name: syn_c_retired -> retired."""
    return name[len(SYN_PREFIX) :] if name.startswith(SYN_PREFIX) else name


# Views over METRICS. Derived from the definitions, so they cannot drift.
METRIC_BY_NAME: Dict[str, Metric] = {m.name: m for m in METRICS}
FORMULAS: Dict[str, str] = {m.name: m.expr for m in METRICS}
DERIVED_NAMES: Set[str] = set(METRIC_BY_NAME)
COUNTER_METRICS: List[Metric] = [m for m in METRICS if m.level == 0 and not m.custom]
TOPDOWN_METRICS: List[Metric] = [m for m in METRICS if m.level >= 1]
CUSTOM_METRICS: List[Metric] = [m for m in METRICS if m.custom]

SUBCORE_METRICS: List[Metric] = [m for m in METRICS if MetricScope.SUBCORE in scope_of(m)]
VF_METRICS: List[Metric] = [m for m in METRICS if MetricScope.VF in scope_of(m)]

# Display names of the top-down hierarchy nodes; reporters validate against
# these, and against the per-VF subset when they plot per-VF charts.
TOPDOWN_DISPLAY_NAMES: Set[str] = {display_name(m.name) for m in TOPDOWN_METRICS}
VF_DISPLAY_NAMES: Set[str] = {display_name(m.name) for m in VF_METRICS}
SUBCORE_DISPLAY_NAMES: Set[str] = {display_name(m.name) for m in SUBCORE_METRICS}

# Structural sanity, checked once at import.
assert len(METRIC_BY_NAME) == len(METRICS), "duplicate metric name"
for _m in METRICS:
    assert (_m.parent is None) or (_m.level >= 1), f"{_m.name}: level 0 metric cannot have a parent"
    assert _m.parent is None or _m.parent in METRIC_BY_NAME, f"{_m.name}: unknown parent {_m.parent}"
    if _m.parent is not None:
        assert METRIC_BY_NAME[_m.parent].level == _m.level - 1, f"{_m.name}: parent must sit exactly one level up"
        # A node plotted per VF needs its parent per VF too, otherwise the
        # "share of parent" column has nothing to divide by.
        assert MetricScope.VF not in scope_of(_m) or MetricScope.VF in scope_of(METRIC_BY_NAME[_m.parent]), (
            f"{_m.name}: per-VF node has a subcore-only parent"
        )


def _probe_dependencies() -> Dict[str, Set[str]]:
    """Metric -> the metrics it reads, discovered by running each fn once.

    CAVEAT: exact only while every fn reads its inputs unconditionally (true
    today). A conditional read like `c["a"] if c["b"] > 0 else 0.0` would
    hide `a`, so this probes with several different fills and unions the
    reads as a cheap guard against that.
    """
    fills: List[Dict[str, float]] = [
        {},
        dict.fromkeys(_ALL_READ_NAMES, 1.0),
        {name: float(i + 1) for i, name in enumerate(sorted(_ALL_READ_NAMES))},
    ]
    deps: Dict[str, Set[str]] = {m.name: set() for m in METRICS}
    for fill in fills:
        for m in METRICS:
            c = Ctx(fill)
            m.fn(c)
            deps[m.name] |= c.reads & DERIVED_NAMES
    return deps


def _first_pass_read_names() -> Set[str]:
    """Every key any fn touches on an empty context (raw + derived)."""
    names: Set[str] = set()
    for m in METRICS:
        c = Ctx({})
        m.fn(c)
        names |= c.reads
    return names


_ALL_READ_NAMES: Set[str] = _first_pass_read_names()
DEPS: Dict[str, Set[str]] = _probe_dependencies()


def required_metrics(seeds: Set[str]) -> Set[str]:
    """Transitive closure of `seeds` over DEPS: what must be computed."""
    need: Set[str] = set()
    stack = list(seeds)
    while stack:
        name = stack.pop()
        if name in need:
            continue
        need.add(name)
        stack.extend(DEPS[name])
    return need


SUBCORE_REQUIRED: Set[str] = required_metrics({m.name for m in SUBCORE_METRICS})
VF_REQUIRED: Set[str] = required_metrics({m.name for m in VF_METRICS})

# A dependency must be defined before its dependent, or a single pass over
# METRICS would read a stale zero.
_ORDER = {m.name: i for i, m in enumerate(METRICS)}
for _name, _dep_set in DEPS.items():
    for _dep in _dep_set:
        assert _ORDER[_dep] < _ORDER[_name], f"{_name} depends on {_dep}, which is defined later"


def evaluate(
    raw: Dict[str, float],
    required: Optional[Set[str]] = None,
) -> Tuple[Dict[str, RawUsage], Dict[str, float]]:
    """Compute every synthetic counter from a dict of raw delta counters.

    Returns (raw_used, values). `required` (e.g. VF_REQUIRED) limits
    computation to that closure; filter with values_for_scope() before
    persisting, since the closure can be wider than the target scope.

    When syn_c_optimal <= 0 (e.g. a VF that burned no vector cycles), the
    top-down hierarchy is undefined and level >= 1 metrics - and custom
    ones, which depend on them - are skipped entirely rather than emitted as
    zeros.
    """
    c = Ctx(raw)
    values: Dict[str, float] = {}

    def compute(metrics: List[Metric]) -> None:
        for m in metrics:
            if required is None or m.name in required:
                values[m.name] = c[m.name] = m.fn(c)

    compute(COUNTER_METRICS)
    if c["syn_c_optimal"] > 0:
        compute(TOPDOWN_METRICS)
        compute(CUSTOM_METRICS)

    return _collect_raw_usage(c), values


def _collect_raw_usage(c: Ctx) -> Dict[str, RawUsage]:
    """Raw counters the formulas actually read, with their present flag."""
    # Snapshot the names first: reading c[name] below would otherwise mutate
    # c.reads while we iterate it.
    raw_names = sorted(c.reads - DERIVED_NAMES)
    return {name: RawUsage(value=c[name], present=c.is_present(name)) for name in raw_names}


def values_for_scope(values: Dict[str, float], scope: MetricScope) -> Dict[str, float]:
    """Subset of `values` that this scope persists."""
    return {m.name: values[m.name] for m in METRICS if scope in scope_of(m) and m.name in values}
