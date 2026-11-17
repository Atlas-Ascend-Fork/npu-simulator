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

"""Materialize UI-independent roofline metrics from helper contract v6/v7.

Calculation conventions follow the restored historical roofline implementation;
results and architecture references are persisted in analysis tables.
Diagnostic counts are emitted only at DEBUG level.
"""

import json
import logging as log
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from npusim.prof.src.arch_diagram.constants import MEMORY_BANDWIDTH_V100
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.analyzers.vector_utilization_analyzer import VectorUtilizationAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.database_manager import BackendDBManager
from npusim.prof.src.common.biprof_constants import BYTES_IN_GB, core_num_map, frequency_ghz_map, peak_tflops_map
from npusim.prof.src.common.biprof_enum import ArchName
from npusim.prof.src.common.extend_params import ExtendParams


@dataclass
class RooflinePoint:
    function_pc: int
    function_size: int
    function_name: str
    execution_mode: str
    unit: str
    memory_level: str
    execution_count: int = 0
    duration_ticks: int = 0
    fp_operations: int = 0
    integer_operations: int = 0
    total_operations: int = 0
    processed_bytes: int = 0
    arithmetic_intensity: float = 0.0
    throughput_gbs: float = 0.0
    performance_gflops: float = 0.0


class RooflineAnalyzer(IAnalyzer):
    """Persist BiProf-compatible operation rates, timing and chip references."""

    _dtype = re.compile(r"\.(bf16|fp16|fp32|f16|f32|s8|u8|s16|u16|s32|u32|b8|b16|b32)(?=[\s,.]|$)", re.I)
    _simt_int = {"SIMT_LEA", "SIMT_LEA_HI_X", "SIMT_SHF", "SIMT_SHFI", "SIMT_PLOP3", "SIMT_LOP3", "SIMT_LOP3_I"}

    def __init__(self):
        self.diagnostics = Counter()

    @staticmethod
    def _function(pc, functions):
        return next((f for f in functions if f["pc"] <= pc < f["pc"] + f["size"]), None)

    def _simd_work(self, row, ep, text):
        name = row["ExecInstrName"]
        if name not in VectorUtilizationAnalyzer.supported_calc_instrs:
            self.diagnostics["excluded_simd_opcode:" + name] += 1
            return 0, 0
        match = self._dtype.search(text)
        dtype = ep.get("dtype") or (match.group(1) if match else None)
        if not dtype:
            self.diagnostics["missing_simd_dtype:" + name] += 1
            return None
        dtype = dtype.lower().replace("fp", "f")
        count = ep.get("processed_elements")
        if count is None:
            pb = ep.get("processed_bytes")
            if pb is None:
                self.diagnostics["missing_simd_elements:" + name] += 1
                return None
            size = IAnalyzer.get_dtype_size(dtype)
            if size <= 0 or pb % size:
                self.diagnostics["invalid_simd_bytes:" + name] += 1
                return None
            count = pb // size
        count *= 2 if name in ("RV_VMULA", "RV_VMADD") else 1
        return (0, count) if dtype.startswith(("u", "s", "type16")) else (count, 0)

    def _simt_work(self, row, ep):
        name = row["ExecInstrName"]
        if ep.get("exec_mask") is None or ep.get("prdct_mask") is None:
            self.diagnostics["missing_simt_mask:" + name] += 1
            return None
        count = bin(int(ep["exec_mask"]) & int(ep["prdct_mask"])).count("1")
        if name.startswith("SIMT_F") or name == "SIMT_MUFU":
            return count * (2 if name.startswith("SIMT_FFMA") else 1), 0
        if name.startswith("SIMT_I") or name in self._simt_int:
            return 0, count * (2 if name.startswith("SIMT_IMAD") else 1)
        return 0, 0

    def _simt_bytes(self, row, ep):
        dtype = ExtendParams(ep).access_btype
        if dtype is None or ep.get("exec_mask") is None or ep.get("prdct_mask") is None:
            self.diagnostics["missing_simt_memory:" + row["ExecInstrName"]] += 1
            return None
        lanes = bin(int(ep["exec_mask"]) & int(ep["prdct_mask"])).count("1")
        return lanes * IAnalyzer.get_dtype_size(dtype.lower())

    @staticmethod
    def _table_bytes(table):
        if not database_handler.table_exists(table):
            return {}
        return {
            r["ExecInstrId"]: r["ProcessedBytes"]
            for r in database_handler.execute("SELECT ExecInstrId, ProcessedBytes FROM " + table).fetchall()
            if r["ProcessedBytes"] is not None
        }

    def _calculate(self):
        self.diagnostics.clear()
        selected = set(BackendDBManager.get_active_cores())
        rows = [
            dict(r)
            for r in database_handler.execute("SELECT * FROM ExecutedInstructions").fetchall()
            if r["ExecInstrCoreId"] in selected
        ]
        functions = [
            dict(pc=r["FunctionAddr"], size=r["FunctionSize"], name=r["FunctionName"])
            for r in database_handler.execute("SELECT * FROM Functions").fetchall()
        ]
        if not functions:
            raise ValueError(
                "Roofline needs function ranges; supply the device ELF through --object-file in BIPROF_OPTS"
            )
        text = {
            r["SourceInstrAddr"]: r["SourceInstrText"] or ""
            for r in database_handler.execute(
                "SELECT SourceInstrAddr, SourceInstrText FROM SourceInstructions"
            ).fetchall()
        }
        facts = {r["ExecInstrId"]: json.loads(r["ExtendParams"]) for r in rows}
        (ub_bytes, simt_ub_bytes, simt_out_bytes, simd_bytes, dma_bytes, fixp_bytes, fixp_masks) = self._memory_facts()
        stats, intervals, work, invalid = {}, defaultdict(list), defaultdict(lambda: [0, 0]), set()
        mapped = {}
        for row in rows:
            func = self._function(row["SourceInstrAddr"], functions)
            if func is None:
                self.diagnostics["unmapped_pc"] += 1
                continue
            mapped[row["ExecInstrId"]] = func
            ep = facts[row["ExecInstrId"]]
            name = row["ExecInstrName"]
            mode = "CUBE" if ep.get("core_type") == "AIC" else ("SIMT" if name.startswith("SIMT_") else "SIMD")
            key = (func["pc"], mode)
            result = self._instruction_work(row, ep, text)
            # Match BiProf's partial joins: unavailable facts contribute
            # nothing, but must not discard valid facts from the same function.
            if result is not None:
                work[key][0] += result[0]
                work[key][1] += result[1]
            labels = []
            if mode == "SIMT":
                labels = self._simt_memory(row, ep, simt_ub_bytes, simt_out_bytes)
            elif name.startswith("RV_V") and ep.get("instr_type") in ("RVECLD", "RVECST"):
                labels = self._simd_memory(row, ep, simd_bytes, ub_bytes)
            elif mode == "CUBE":
                labels = self._cube_memory(row, ep, dma_bytes, fixp_bytes, fixp_masks)
            self._add_memory(stats, key, func, mode, name, labels)
            if mode == "CUBE":
                intervals[key].append((row["ExecInstrTickStart"], row["ExecInstrTickEnd"]))
        counts = self._vf_intervals(rows, facts, mapped, intervals, invalid)
        points, recorded_frequency = self._make_points(stats, intervals, work, invalid, counts)
        return points, sorted(selected), self._references(rows, recorded_frequency)

    def _add_memory(self, stats, key, func, mode, name, labels):
        if any(pb is None for _, pb in labels):
            self.diagnostics["missing_memory_bytes:" + name] += 1
        for label, pb in labels:
            skey = key + (label,)
            stats.setdefault(
                skey,
                RooflinePoint(func["pc"], func["size"], func["name"], mode, "AIC" if mode == "CUBE" else "AIV", label),
            )
            if pb is not None:
                stats[skey].processed_bytes += pb

    def _memory_facts(self):
        ub_bytes = defaultdict(int)
        if database_handler.table_exists("UbRwStatistics"):
            for r in database_handler.execute("SELECT ExecInstrId, ProcessedBytes FROM UbRwStatistics").fetchall():
                ub_bytes[r["ExecInstrId"]] += r["ProcessedBytes"]
        simt_ub_bytes = defaultdict(int)
        if database_handler.table_exists("UbRwStatistics"):
            for r in database_handler.execute("""
                SELECT ExecInstrId, ProcessedBytes FROM UbRwStatistics
                WHERE UbAccessType IN (1, 2)
            """).fetchall():
                simt_ub_bytes[r["ExecInstrId"]] += r["ProcessedBytes"]
        simt_out_bytes = self._table_bytes("SIMTLdStProcessedBytes")
        simd_bytes = (
            self._table_bytes("VectorUtilizations") if database_handler.table_exists("VectorUtilizations") else None
        )
        dma_bytes = self._table_bytes("DmaMovProcessedBytes")
        fixp_bytes = self._table_bytes("FixpParams")
        fixp_masks = {}
        if database_handler.table_exists("FixpParams"):
            fixp_masks = {
                r["ExecInstrId"]: r["DstUBMask"]
                for r in database_handler.execute("SELECT ExecInstrId, DstUBMask FROM FixpParams").fetchall()
            }
        return ub_bytes, simt_ub_bytes, simt_out_bytes, simd_bytes, dma_bytes, fixp_bytes, fixp_masks

    def _instruction_work(self, row, ep, text):
        name = row["ExecInstrName"]
        result = (0, 0)
        if name == "MMAD":
            if any(ep.get(k) is None for k in ("m", "n", "k", "matrix_c_dtype")):
                result = None
                self.diagnostics["missing_mmad_facts"] += 1
            else:
                nops = 2 * ep["m"] * ep["n"] * ep["k"]
                result = (0, nops) if ep["matrix_c_dtype"].startswith(("u", "s", "type16")) else (nops, 0)
        elif name.startswith("SIMT_") and ep.get("instr_type") == "RVECEX":
            result = self._simt_work(row, ep)
        elif name.startswith("RV_") and ep.get("instr_type") == "RVECEX":
            result = self._simd_work(row, ep, text.get(row["SourceInstrAddr"], ""))
        return result

    def _simt_memory(self, row, ep, simt_ub_bytes, simt_out_bytes):
        labels = []
        name = row["ExecInstrName"]
        # BiProf uses recorded UB traffic when available, including its
        # read/write enum convention; cache/global rows are not filtered.
        if row["ExecInstrId"] in simt_ub_bytes:
            labels.append(("SIMT-UB", simt_ub_bytes[row["ExecInstrId"]]))
        elif name in ("SIMT_LDS", "SIMT_STS"):
            labels.append(("SIMT-UB", self._simt_bytes(row, ep)))
        if name in ("SIMT_LDG", "SIMT_STG"):
            pb = simt_out_bytes.get(row["ExecInstrId"])
            labels.append(("SIMT-OUT", pb if pb is not None else self._simt_bytes(row, ep)))
        return labels

    def _simd_memory(self, row, ep, simd_bytes, ub_bytes):
        labels = []
        name = row["ExecInstrName"]
        # BiProf sums VectorUtilizations, whose producer supports only
        # specific load/store opcodes. Do not add scatter traffic from
        # UbRwStatistics to that sum or fill holes in a partial table.
        if name in VectorUtilizationAnalyzer.supported_ldst_instrs or (
            simd_bytes is not None and row["ExecInstrId"] in simd_bytes
        ):
            if simd_bytes is not None:
                pb = simd_bytes.get(row["ExecInstrId"])
            else:
                pb = ep.get("processed_bytes")
                if pb is None:
                    pb = ub_bytes.get(row["ExecInstrId"])
            labels.append(("SIMD-UB", pb))
        else:
            self.diagnostics["excluded_simd_memory_opcode:" + name] += 1
        return labels

    def _cube_memory(self, row, ep, dma_bytes, fixp_bytes, fixp_masks):
        labels = []
        # Match BiProf's MTE2/FIXP selection, not the MTE1 L1->L0 path.
        pb = None
        if ep.get("instr_type") == "MTE2":
            pb = dma_bytes.get(row["ExecInstrId"], ep.get("processed_bytes"))
        elif ep.get("instr_type") == "FIXP":
            pb = fixp_bytes.get(row["ExecInstrId"], ep.get("processed_bytes"))
        # Queue tags also occur on barriers and scalar setup. A byte
        # metric or memory endpoints identify an actual transfer.
        if ep.get("instr_type") in ("MTE2", "FIXP") and (pb is not None or (ep.get("src_mem") and ep.get("dst_mem"))):
            labels.append(("CUBE-L0", pb))
            if ep.get("src_mem") == "OUT" and ep.get("dst_mem") == "L1":
                labels.append(("CUBE-OUT", pb))
            elif ep.get("src_mem") in ("L0C", "L0C32") and ep.get("dst_mem") == "OUT":
                mask = fixp_masks.get(row["ExecInstrId"], ep.get("dst_ub_mask"))
                labels.append(("CUBE-OUT", pb / 2 if pb is not None and mask == 3 else pb))
        return labels

    def _vf_intervals(self, rows, facts, mapped, intervals, invalid):
        counts = Counter()
        # A VF command can be queued while the preceding VF is still executing.
        # Its recorded start therefore includes queueing time. Completion ticks
        # delimit execution windows separately on each core/subcore. Derive
        # these windows locally: roofline must not depend on smart-pipes rewriting
        # ExecutedInstructions. LP/scalar setup is not evidence of the callee.
        calls = sorted(
            (r for r in rows if r["ExecInstrName"] in ("VF", "VFI", "VF_SIMT")),
            key=lambda r: (
                r["ExecInstrCoreId"],
                r["CoreTypeId"],
                r["ExecInstrTickEnd"],
                r["ExecInstrTickStart"],
                r["ExecInstrId"],
            ),
        )
        previous_ends = {}
        for call in calls:
            scope = (call["ExecInstrCoreId"], call["CoreTypeId"])
            end = call["ExecInstrTickEnd"]
            start = max(call["ExecInstrTickStart"], previous_ends.get(scope, call["ExecInstrTickStart"]))
            previous_ends[scope] = end
            mode = "SIMT" if call["ExecInstrName"] == "VF_SIMT" else "SIMD"
            candidates = set()
            for row in rows:
                ep = facts[row["ExecInstrId"]]
                if (
                    row["ExecInstrId"] in mapped
                    and row["ExecInstrCoreId"] == call["ExecInstrCoreId"]
                    and row["CoreTypeId"] == call["CoreTypeId"]
                    and ep.get("instr_type") in ("RVECEX", "RVECLD", "RVECST")
                    and row["ExecInstrName"].startswith("SIMT_" if mode == "SIMT" else "RV_")
                    and row["ExecInstrTickStart"] >= start
                    and row["ExecInstrTickStart"] < end
                    and row["ExecInstrTickEnd"] <= end
                ):
                    candidates.add(mapped[row["ExecInstrId"]]["pc"])
            if len(candidates) != 1:
                self.diagnostics["ambiguous_vf_call"] += 1
                invalid.update((pc, mode) for pc in candidates)
                continue
            key = (next(iter(candidates)), mode)
            # The narrowed execution window identifies the callee only.
            # BiProf's rate denominator sums the original VF durations.
            intervals[key].append((call["ExecInstrTickStart"], end))
            counts[key] += 1
        return counts

    def _make_points(self, stats, intervals, work, invalid, counts):
        recorded_frequency = float(database_handler.get_frequency_ghz())
        if not math.isfinite(recorded_frequency) or recorded_frequency <= 0:
            raise ValueError("Roofline needs a finite positive KernelInfo.frequency_ghz")
        frequency = frequency_ghz_map[ArchName.V100]
        points = []
        for key, stat in sorted(stats.items()):
            func_pc, mode, label = key
            wkey = key[:2]
            fp, integer = work[wkey]
            spans = intervals[wkey]
            ticks = (
                max(end for _, end in spans) - min(start for start, _ in spans)
                if mode == "CUBE" and spans
                else sum(end - start for start, end in spans)
            )
            if wkey in invalid or not ticks or not (fp + integer) or not stat.processed_bytes:
                self.diagnostics["omitted_point:" + label] += 1
                continue
            stat.execution_count = counts[wkey] if mode != "CUBE" else 1
            stat.duration_ticks = ticks
            stat.fp_operations, stat.integer_operations = fp, integer
            stat.total_operations = fp + integer
            # Coordinates: O = FP + integer operations, B = bytes, T = ticks.
            # AI = O/B; seconds = T/(GHz * 1e9). Rates use binary GB (2**30).
            # Performance = AI * bandwidth = O / 2**30 / seconds.
            # Keep this multiplication order for BiProf numerical compatibility.
            stat.arithmetic_intensity = stat.total_operations / stat.processed_bytes
            seconds = ticks / (frequency * 1.0e9)
            stat.throughput_gbs = stat.processed_bytes / BYTES_IN_GB / seconds
            stat.performance_gflops = stat.arithmetic_intensity * stat.throughput_gbs
            points.append(asdict(stat))
        return points, recorded_frequency

    @staticmethod
    def get_name():
        return "roofline"

    @staticmethod
    def _references(rows, frequency):
        arch = ArchName.V100
        ncores = core_num_map[arch]
        aic = len({r["ExecInstrCoreId"] for r in rows if r["CoreTypeId"] == 1})
        aiv = len({(r["ExecInstrCoreId"], r["CoreTypeId"]) for r in rows if r["CoreTypeId"] in (2, 3)})
        selected = len({r["ExecInstrCoreId"] for r in rows})
        peaks = peak_tflops_map[arch]
        bw = MEMORY_BANDWIDTH_V100
        # BiProf stores full-chip ceilings; active counts are metadata only.
        metadata = ("v100", frequency, frequency_ghz_map[arch], selected, ncores, aic, aiv)
        compute_peaks = [
            ("AIV", dtype, peaks[key] * 1000)
            for dtype, key in (("fp16", "vector_fp16_FMA"), ("bf16", "vector_fp16_bf16"), ("fp32", "vector_fp32"))
        ]
        compute_peaks.extend(
            ("AIC", key[len("cube_") :], peaks[key] * 1000)
            for key in ("cube_fp16_bf16", "cube_tf32_hf32", "cube_fp32", "cube_int8_fp8")
        )
        bandwidth_peaks = [
            ("AIV", "SIMD-UB", (bw["aiv0_ub_to_simd"] + bw["aiv1_ub_to_simd"]) * ncores),
            ("AIV", "SIMT-UB", (bw["aiv0_ub_to_simt"] + bw["aiv1_ub_to_simt"]) * ncores),
            ("AIV", "SIMT-OUT", bw["chip_to_out"]),
            ("AIC", "CUBE-L0", (bw["aic_l1_to_l0a"] + bw["aic_l1_to_l0b"] + bw["aic_l0c_to_all_syn"]) * ncores),
            ("AIC", "CUBE-OUT", bw["chip_to_out"]),
        ]
        return metadata, compute_peaks, bandwidth_peaks

    def process(self):
        # One snapshot across all tables; the analyzer manager commits it before
        # any reporter runs. Re-running this analyzer replaces its own rows only.
        database_handler.execute("SAVEPOINT roofline_analysis")
        try:
            points, selected, references = self._calculate()
            metadata, compute_peaks, bandwidth_peaks = references
            for table in ("RooflineMetadata", "RooflinePoints", "RooflineComputePeaks", "RooflineBandwidthPeaks"):
                database_handler.execute("DELETE FROM " + table)
            database_handler.execute("INSERT INTO RooflineMetadata VALUES (1, ?, ?, ?, ?, ?, ?, ?)", metadata)
            database_handler.executemany(
                "INSERT INTO RooflinePoints VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        p["function_pc"],
                        p["function_size"],
                        p["function_name"],
                        p["execution_mode"],
                        p["unit"],
                        p["memory_level"],
                        p["execution_count"],
                        p["duration_ticks"],
                        p["fp_operations"],
                        p["integer_operations"],
                        p["total_operations"],
                        p["processed_bytes"],
                        p["arithmetic_intensity"],
                        p["throughput_gbs"],
                        p["performance_gflops"],
                    )
                    for p in points
                ],
            )
            database_handler.executemany("INSERT INTO RooflineComputePeaks VALUES (?, ?, ?)", compute_peaks)
            database_handler.executemany("INSERT INTO RooflineBandwidthPeaks VALUES (?, ?, ?)", bandwidth_peaks)
        except Exception:
            database_handler.execute("ROLLBACK TO roofline_analysis")
            database_handler.execute("RELEASE roofline_analysis")
            raise
        database_handler.execute("RELEASE roofline_analysis")
        for code, count in sorted(self.diagnostics.items()):
            log.debug("Roofline diagnostic %s: %d", code, count)
