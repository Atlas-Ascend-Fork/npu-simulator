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

import bisect
import math
from typing import Dict, Any, List, Tuple
from collections import defaultdict

from npusim.prof_demo.analysis.bandwidth import BandwidthAnalyzer
from npusim.prof_demo.database import database_handler, BackendDBManager
from npusim.prof_demo.enums import CoreType, InstrQueueType, InstrType


class SummaryReporter:
    @classmethod
    def get_kernel_summary_info(cls) -> Dict[str, Any]:
        msg = """
        SELECT KernelInfo.KernelInfoAttr AS Name,
               KernelInfo.KernelInfoVal AS Val
        FROM sim.KernelInfo
        WHERE Name = 'exec_time_ticks'
        OR Name = 'instrs_exec_num'
        OR Name = 'ai_core_active'
        OR Name = 'frequency_ghz'
        """
        kernel_info_data = database_handler.execute(msg).fetchall()
        res: Dict[str, int] = {}
        for line in kernel_info_data:
            if line["Name"] == "exec_time_ticks":
                res["kernel_total_clocks"] = line["Val"]
            elif line["Name"] == "instrs_exec_num":
                res["kernel_instructions_executed"] = line["Val"]
            elif line["Name"] == "ai_core_active":
                res["ai_core_active"] = line["Val"]
            elif line["Name"] == "frequency_ghz":
                res["frequency_ghz"] = line["Val"]
        return res

    @classmethod
    def get_pipe_utilization(cls, ai_core_active: int) -> Dict[str, Dict[str, Any]]:
        cursor = database_handler.execute("""
        SELECT CoreId, CoreTypeId, InstrQueueTypeId, PipeUtilization
        FROM main.PipesUtilization
        """)
        pipe_values: Dict[Tuple[str, str], List[float]] = {}
        for _, core_type_id, pipe_type_id, util in cursor:
            core_type = CoreType(core_type_id).name
            pipe_type = InstrQueueType(pipe_type_id).name

            pipe_values.setdefault((core_type, pipe_type), []).append(util)

        pipeline_util_summary: Dict[str, Any] = {}
        for pipe, vals in pipe_values.items():
            pipe_mean = sum(vals) / len(vals) if vals else 0
            pipe_max = max(vals) if vals else 0
            pipe_min = min(vals) if vals else 0
            if math.isclose(pipe_mean, 0.0) and math.isclose(pipe_max, 0.0) and math.isclose(pipe_min, 0.0):
                continue
            pipeline_util_summary[f"{pipe[0]}_{pipe[1]}"] = {
                "mean": round(pipe_mean / 100, 4),
                "max": round(pipe_max / 100, 4),
                "min": round(pipe_min / 100, 4),
            }
            if ai_core_active > 1:
                pipeline_util_summary[f"{pipe[0]}_{pipe[1]}"]["per_core"] = [round(val / 100, 4) for val in vals]

        return {
            "pipeline_util_summary": pipeline_util_summary,
        }

    @classmethod
    def get_imbalance_ratio(cls) -> float:
        cursor = database_handler.execute("""
        SELECT ExecInstrCoreId,
            MAX(ExecInstrTickEnd) - MIN(ExecInstrTickStart) AS KernelDuration
        FROM ExecutedInstructions
        GROUP BY ExecInstrCoreId
        """)

        kernel_duration_per_core = {row[0]: row[1] for row in cursor}
        durations = list(kernel_duration_per_core.values())
        max_dur = max(durations)
        min_dur = min(durations)
        ratio = round(max_dur / min_dur, 4) if min_dur else None
        return ratio

    @classmethod
    def get_top_level_diagnosis(cls, util_summary: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        max_pipe, max_stats = max(
            util_summary.items(), key=lambda item: item[1]["mean"], default=(None, {"mean": None})
        )
        imbalance_ratio = cls.get_imbalance_ratio()
        return {
            "dominant_pipeline": max_pipe,
            "dominant_pipeline_util": max_stats["mean"],
            "imbalance_ratio": imbalance_ratio,
        }

    @classmethod
    def get_total_scalar_instrs_count(cls, core_type: int) -> int:
        msg = """
        SELECT COUNT(ExecInstrCoreId) AS ScalarCount
        FROM ExecutedInstructions
        WHERE InstrTypeId = {}
        AND CoreTypeId = {}
        """.format(InstrType.SCALAR.value, core_type)
        scalar_count = database_handler.execute(msg).fetchall()[0]["ScalarCount"]
        return scalar_count

    @classmethod
    def get_load_count(cls, core_type: int) -> int:
        msg = """
        SELECT COUNT(ExecInstrCoreId) AS ScalarCount
        FROM ExecutedInstructions
        WHERE InstrTypeId = {}
        AND ExecInstrName LIKE 'LD%'
        AND CoreTypeId = {}
        """.format(InstrType.SCALAR.value, core_type)
        load_count = database_handler.execute(msg).fetchall()[0]["ScalarCount"]
        return load_count

    @classmethod
    def get_store_count(cls, core_type: int) -> int:
        msg = """
        SELECT COUNT(ExecInstrCoreId) AS ScalarCount
        FROM ExecutedInstructions
        WHERE InstrTypeId = {}
        AND ExecInstrName LIKE 'ST%'
        AND CoreTypeId = {}
        """.format(InstrType.SCALAR.value, core_type)
        store_count = database_handler.execute(msg).fetchall()[0]["ScalarCount"]
        return store_count

    @classmethod
    def get_scalar_instructions_summary(cls) -> Dict[str, Dict[str, float]]:
        core_types = BackendDBManager.get_active_core_types()
        res = {}
        for core_type in core_types:
            total_count = cls.get_total_scalar_instrs_count(core_type)
            load_count = cls.get_load_count(core_type)
            store_count = cls.get_store_count(core_type)
            rat = round((load_count + store_count) / total_count, 2) if total_count else 0
            res[CoreType(core_type).name] = {
                "total_count": total_count,
                "load_count": load_count,
                "store_count": store_count,
                "load_store_ratio": rat,
            }
        return res

    @classmethod
    def get_rvec_counts(cls) -> Tuple[int, int, int]:
        msg = """
        SELECT InstrTypeHistCount.Type AS Type,
               InstrTypeHistCount.Count AS Count
        FROM   main.InstrTypeHistCount
        WHERE Type = 'RVECEX'
        OR Type = 'RVECLD'
        OR Type = 'RVECST'
        """
        RVECEX_count = RVECLD_count = RVECST_count = 0
        types = database_handler.execute(msg).fetchall()
        for t in types:
            if t["Type"] == "RVECEX":
                RVECEX_count = t["Count"]
            elif t["Type"] == "RVECLD":
                RVECLD_count = t["Count"]
            elif t["Type"] == "RVECST":
                RVECST_count = t["Count"]
        return RVECEX_count, RVECLD_count, RVECST_count

    @classmethod
    def get_avg_simd_ipc(cls) -> Tuple[float, float, float]:
        msg = """
        SELECT AVG(ScalarIPC) AS AVGScalarIPC,
               AVG(ExecIPC) AS AVGExecIPC,
               AVG(LdStIPC) AS AVGLdStIPC
        FROM   main.VfIPC
        """
        res = database_handler.execute(msg).fetchall()
        SIMD_ScalarIPC = res[0]["AVGScalarIPC"]
        SIMD_ExecIPC = res[0]["AVGExecIPC"]
        SIMD_LdStIPC = res[0]["AVGLdStIPC"]
        return SIMD_ScalarIPC, SIMD_ExecIPC, SIMD_LdStIPC

    @classmethod
    def get_avg_simt_ipc(cls) -> Tuple[float, float, float]:
        msg = """
        SELECT AVG(ExecIPC) AS AVGExecIPC,
               AVG(LdStIPC) AS AVGLdStIPC,
               AVG(BranchIPC) AS AVGBranchIPC
        FROM   main.VfSimtIPC
        """
        res = database_handler.execute(msg).fetchall()
        SIMT_ExecIPC = res[0]["AVGExecIPC"]
        SIMT_LdStIPC = res[0]["AVGLdStIPC"]
        SIMT_BranchIPC = res[0]["AVGBranchIPC"]
        return SIMT_ExecIPC, SIMT_LdStIPC, SIMT_BranchIPC

    @classmethod
    def get_vector_instructions_summary(cls) -> Dict[str, float]:
        RVECEX_count, RVECLD_count, RVECST_count = cls.get_rvec_counts()
        SIMD_ScalarIPC, SIMD_ExecIPC, SIMD_LdStIPC = cls.get_avg_simd_ipc()
        SIMT_ExecIPC, SIMT_LdStIPC, SIMT_BranchIPC = cls.get_avg_simt_ipc()
        ipc_metrics = {
            "SIMD_ScalarIPC": SIMD_ScalarIPC,
            "SIMD_ExecIPC": SIMD_ExecIPC,
            "SIMD_LdStIPC": SIMD_LdStIPC,
            "SIMT_ExecIPC": SIMT_ExecIPC,
            "SIMT_LdStIPC": SIMT_LdStIPC,
            "SIMT_BranchIPC": SIMT_BranchIPC,
        }
        return {
            "RVECEX_count": RVECEX_count,
            "RVECLD_count": RVECLD_count,
            "RVECST_count": RVECST_count,
            "ub_traffic_ratio": (RVECLD_count + RVECST_count) / RVECEX_count if RVECEX_count else 0,
            **{k: v for k, v in ipc_metrics.items() if v is not None},
        }

    @classmethod
    def get_icache_refill_stat(cls) -> Tuple[int, int]:
        # The two-table contract has no cache-event producer.
        return 0, None

    @classmethod
    def get_cache_metrics(cls) -> Dict[str, float]:
        icache_refill_count, icache_refill_ticks = cls.get_icache_refill_stat()
        return {
            "icache_refill_count": icache_refill_count,
            "icache_refill_ticks": icache_refill_ticks,
        }

    # Public summary key -> BandwidthAnalyzer metric prefix, in output order.
    BANDWIDTH_ENTRIES = (
        ("AIC_MTE2_OUT_TO_L1", "aic_out_to_l1"),
        ("AIC_MTE1_L1_TO_L0A", "aic_l1_to_l0a"),
        ("AIC_MTE1_L1_TO_L0B", "aic_l1_to_l0b"),
        ("AIC_FIXPIPE_L0C_TO_L1", "aic_l0c_to_l1"),
        ("AIC_FIXPIPE_L0C_TO_OUT", "aic_l0c_to_out"),
        ("AIC_FIXPIPE_L0C_TO_UB0", "aic_l0c_to_ub0"),
        ("AIC_FIXPIPE_L0C_TO_UB1", "aic_l0c_to_ub1"),
        ("AIV0_MTE2_OUT_TO_UB", "aiv0_out_to_ub"),
        ("AIV0_MTE3_UB_TO_OUT", "aiv0_ub_to_out"),
        ("AIV0_MTE3_UB_TO_L1", "aiv0_ub_to_l1"),
        ("AIV1_MTE2_OUT_TO_UB", "aiv1_out_to_ub"),
        ("AIV1_MTE3_UB_TO_OUT", "aiv1_ub_to_out"),
        ("AIV1_MTE3_UB_TO_L1", "aiv1_ub_to_l1"),
    )

    @classmethod
    def get_bandwidth_metrics(cls) -> Dict[str, Any]:
        metrics, bandwidth_utilization = BandwidthAnalyzer().compute()
        if len(bandwidth_utilization) != 13:
            raise RuntimeError(
                f"summary bandwidth utilization is incomplete: expected 13 rows, got {len(bandwidth_utilization)}"
            )
        result = {}
        for key, name in cls.BANDWIDTH_ENTRIES:
            # Channels without transactions are omitted.
            if metrics[name + "_cnt"] == 0:
                continue
            result[key] = {
                "total_gbps": metrics[name + "_gbs"],
                "bandwidth_utilization": bandwidth_utilization[name],
                "avg_transaction_gbps": metrics[name + "_ratio"],
                "transaction_count": int(metrics[name + "_cnt"]),
            }
        return result

    @classmethod
    def get_pipeline_overlap_metrics(cls) -> Dict[str, Any]:
        PIPE_PAIRS = [
            (("AIC", "MTE2"), ("AIC", "CUBE"), "AIC_MTE2_vs_AIC_CUBE"),
            (("AIC", "MTE1"), ("AIC", "CUBE"), "AIC_MTE1_vs_AIC_CUBE"),
            (("AIC", "FIXP"), ("AIC", "CUBE"), "AIC_FIXP_vs_AIC_CUBE"),
            (("AIC", "SCALAR"), ("AIC", "CUBE"), "AIC_SCALAR_vs_AIC_CUBE"),
            (("AIV0", "MTE2"), ("AIV0", "VEC"), "AIV0_MTE2_vs_AIV0_VEC"),
            (("AIV0", "SCALAR"), ("AIV0", "VEC"), "AIV0_SCALAR_vs_AIV0_VEC"),
            (("AIV0", "MTE3"), ("AIV0", "VEC"), "AIV0_MTE3_vs_AIV0_VEC"),
            (("AIV1", "MTE2"), ("AIV1", "VEC"), "AIV1_MTE2_vs_AIV1_VEC"),
            (("AIV1", "SCALAR"), ("AIV1", "VEC"), "AIV1_SCALAR_vs_AIV0_VEC"),
            (("AIV1", "MTE3"), ("AIV1", "VEC"), "AIV1_MTE3_vs_AIV1_VEC"),
            (("AIC", "CUBE"), ("AIV0", "VEC"), "AIC_CUBE_vs_AIV0_VEC"),
            (("AIC", "CUBE"), ("AIV1", "VEC"), "AIC_CUBE_vs_AIV1_VEC"),
        ]

        rows = database_handler.execute("""
            SELECT ExecInstrCoreId, CoreTypeId, InstrTypeId,
                ExecInstrTickStart, ExecInstrTickEnd
            FROM ExecutedInstructions
        """).fetchall()

        intervals: dict = defaultdict(lambda: defaultdict(list))
        for r in rows:
            pipe_name = InstrType(r["InstrTypeId"]).name if InstrType(r["InstrTypeId"]) != InstrType.PUSHQ else "VEC"
            key = (CoreType(r["CoreTypeId"]).name, pipe_name)
            intervals[r["ExecInstrCoreId"]][key].append((r["ExecInstrTickStart"], r["ExecInstrTickEnd"]))

        def merge(ivs):
            if not ivs:
                return []
            ivs = sorted(ivs)
            merged = [list(ivs[0])]
            for s, e in ivs[1:]:
                if s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            return [tuple(x) for x in merged]

        def length(ivs):
            return sum(e - s for s, e in ivs)

        def intersect(a, b):
            res, i, j = [], 0, 0
            while i < len(a) and j < len(b):
                lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
                if lo < hi:
                    res.append((lo, hi))
                if a[i][1] < b[j][1]:
                    i += 1
                else:
                    j += 1
            return res

        def union(a, b):
            return merge(a + b)

        merged_by_core = {
            core_id: {key: merge(ivs) for key, ivs in pipes.items()} for core_id, pipes in intervals.items()
        }

        result = {}
        for side_a, side_b, label in PIPE_PAIRS:
            total_inter = 0
            total_union = 0

            for core_pipes in merged_by_core.values():
                ivs_a = core_pipes.get(side_a, [])
                ivs_b = core_pipes.get(side_b, [])
                if not ivs_a or not ivs_b:
                    continue
                total_inter += length(intersect(ivs_a, ivs_b))
                total_union += length(union(ivs_a, ivs_b))

            result[label] = round(total_inter / total_union, 4) if total_union else None

        return result

    @classmethod
    def get_simd_vf_metrics(cls) -> Dict[str, Any]:
        has_simd = database_handler.count_rows("VfIPC") > 0
        if not has_simd:
            return {}

        q1 = database_handler.execute("""
            SELECT
            COUNT(*) AS vf_total_executions,
            COUNT(DISTINCT EI.SourceInstrAddr) AS vf_distinct_count,
            COUNT(DISTINCT EI.ExecInstrCoreId || '_' || EI.CoreTypeId)
            AS vf_active_cores,
            AVG(EI.ExecInstrTickEnd - EI.ExecInstrTickStart) AS vf_avg_cycles,
            MAX(EI.ExecInstrTickEnd - EI.ExecInstrTickStart) AS vf_max_cycles,
            MIN(EI.ExecInstrTickEnd - EI.ExecInstrTickStart) AS vf_min_cycles,
            MIN(VF.ExecIPC) AS vf_min_exec_ipc,
            SUM(EI.ExecInstrTickEnd - EI.ExecInstrTickStart) AS vf_total_cycles
            FROM VfIPC VF
            JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
        """).fetchall()[0]

        q2 = database_handler.execute("""
            SELECT MAX(cnt) AS vf_max_executions_per_call_site
            FROM (
                SELECT COUNT(*) AS cnt
                FROM VfIPC VF
                JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
                GROUP BY EI.SourceInstrAddr
            )
        """).fetchall()[0]

        def ih_count(type_name: str) -> int:
            rows = database_handler.execute(
                "SELECT Count FROM InstrTypeHistCount WHERE Type = ?", (type_name,)
            ).fetchall()
            return rows[0]["Count"] if rows else 0

        # Legacy summary reads the absent clock/name histograms as zero. The
        # two-table contract has no producer for them; keep the public fields.
        rvecsu_count = ih_count("RVECSU")
        pushq_count = ih_count("PUSHQ")
        rvecsu_cycles = pushq_cycles = rvecex_cycles = 0

        rvecex_count, _, _ = cls.get_rvec_counts()

        vf_total_executions = q1["vf_total_executions"] or 0
        vf_total_cycles = q1["vf_total_cycles"] or 0
        vf_min_exec_ipc = q1["vf_min_exec_ipc"]

        vf_active_cores = q1["vf_active_cores"] or 1
        vf_executions_per_core = (
            round(vf_total_executions / vf_active_cores, 1) if vf_active_cores else vf_total_executions
        )

        rvecex_per_vf = round(rvecex_count / vf_total_executions, 3) if vf_total_executions else 0
        rvecex_cycles_per_vf = round(rvecex_cycles / vf_total_executions, 3) if vf_total_executions else 0
        pushq_overhead_ratio = round(pushq_cycles / vf_total_cycles, 4) if vf_total_cycles else 0
        rvecsu_overhead_ratio = round(rvecsu_cycles / vf_total_cycles, 4) if vf_total_cycles else 0

        vf_total_stall_cycles = 0
        vf_stall_event_count = 0

        vf_calls = database_handler.execute("""
            SELECT EI.ExecInstrCoreId AS CoreId,
                   EI.CoreTypeId      AS CoreTypeId,
                   EI.ExecInstrTickStart AS VfStart,
                   EI.ExecInstrTickEnd   AS VfEnd
            FROM VfIPC VF
            JOIN ExecutedInstructions EI ON VF.ExecInstrId = EI.ExecInstrId
        """).fetchall()

        vec_type_ids = (
            InstrType.RVECEX.value,
            InstrType.RVECLP.value,
            InstrType.RVECSU.value,
            InstrType.RVECST.value,
            InstrType.RVECLD.value,
        )
        vec_id_list = ",".join(str(t) for t in vec_type_ids)

        all_vec = database_handler.execute(
            "SELECT ExecInstrCoreId, CoreTypeId, "
            "ExecInstrTickStart, ExecInstrTickEnd "
            "FROM ExecutedInstructions "
            "WHERE InstrTypeId IN ({})".format(vec_id_list)
        ).fetchall()

        vec_by_core = defaultdict(list)
        for r in all_vec:
            vec_by_core[(r["ExecInstrCoreId"], r["CoreTypeId"])].append(r)
        for key in vec_by_core:
            vec_by_core[key].sort(key=lambda r: r["ExecInstrTickStart"])

        for vf in vf_calls:
            key = (vf["CoreId"], vf["CoreTypeId"])
            core_vecs = vec_by_core.get(key, [])
            starts = [r["ExecInstrTickStart"] for r in core_vecs]

            lo = bisect.bisect_left(starts, vf["VfStart"])
            hi = bisect.bisect_left(starts, vf["VfEnd"])

            filtered = [r for r in core_vecs[lo:hi] if r["ExecInstrTickEnd"] <= vf["VfEnd"]]

            ticks_with_issue = set(r["ExecInstrTickStart"] for r in filtered)
            sorted_ticks = sorted(ticks_with_issue)

            prev = vf["VfStart"]
            for tick in sorted_ticks:
                gap = tick - prev
                if gap > 0:
                    vf_stall_event_count += 1
                    vf_total_stall_cycles += gap
                prev = tick + 1
            tail_gap = vf["VfEnd"] - prev
            if tail_gap > 0:
                vf_stall_event_count += 1
                vf_total_stall_cycles += tail_gap

        vf_stall_ratio = round(vf_total_stall_cycles / vf_total_cycles, 4) if vf_total_cycles else 0

        pattern_signals = dict.fromkeys(
            ("rv_vmax", "rv_vcmax", "rv_vadd", "rv_vcadd", "rv_vstui", "rv_vst", "rv_vmul", "rv_vmul_s"), 0
        )

        return {
            "vf_distinct_count": (q1["vf_distinct_count"] or 0),
            "vf_total_executions": vf_total_executions,
            "vf_executions_per_core": vf_executions_per_core,
            "vf_active_cores": vf_active_cores,
            "vf_max_executions_per_call_site": (q2["vf_max_executions_per_call_site"] or 0),
            "vf_avg_cycles": round(q1["vf_avg_cycles"], 3) if q1["vf_avg_cycles"] is not None else 0,
            "vf_max_cycles": (q1["vf_max_cycles"] or 0),
            "vf_min_cycles": (q1["vf_min_cycles"] or 0),
            "vf_min_exec_ipc": vf_min_exec_ipc if vf_min_exec_ipc is not None else 0,
            "rvecsu_count": rvecsu_count,
            "rvecsu_cycles": rvecsu_cycles,
            "pushq_count": pushq_count,
            "pushq_cycles": pushq_cycles,
            "rvecex_per_vf": rvecex_per_vf,
            "rvecex_cycles_per_vf": rvecex_cycles_per_vf,
            "vf_total_cycles": vf_total_cycles,
            "pushq_overhead_ratio": pushq_overhead_ratio,
            "rvecsu_overhead_ratio": rvecsu_overhead_ratio,
            "vf_stall_event_count": vf_stall_event_count,
            "vf_total_stall_cycles": vf_total_stall_cycles,
            "vf_stall_ratio": vf_stall_ratio,
            "pattern_signals": pattern_signals,
        }

    def get_summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        summary["kernel_info"] = self.get_kernel_summary_info()
        summary["pipe_utilization"] = self.get_pipe_utilization(summary["kernel_info"]["ai_core_active"])
        summary["top_level_diagnosis"] = self.get_top_level_diagnosis(
            summary["pipe_utilization"]["pipeline_util_summary"]
        )

        summary["scalar_instructions"] = self.get_scalar_instructions_summary()
        summary["aiv_vector_instructions"] = self.get_vector_instructions_summary()
        summary["cache"] = self.get_cache_metrics()
        summary["bandwidth"] = self.get_bandwidth_metrics()
        summary["pipeline_overlap"] = self.get_pipeline_overlap_metrics()
        summary["simd_vf_metrics"] = self.get_simd_vf_metrics()

        return summary
