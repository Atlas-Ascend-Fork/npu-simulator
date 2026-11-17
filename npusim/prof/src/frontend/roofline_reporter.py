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

"""Export persisted Roofline analysis tables as JSON for external consumers."""

import json
import logging as log
from pathlib import Path

from npusim.prof.src.backend.database import database_handler


class RooflineReporter:
    @staticmethod
    def collect():
        metadata = database_handler.execute("SELECT * FROM RooflineMetadata WHERE AnalysisId=1").fetchone()
        if metadata is None:
            raise ValueError("Roofline analysis data is unavailable; enable the roofline analyzer in BIPROF_OPTS")
        if database_handler.table_exists("AnalysisState"):
            state = database_handler.execute(
                "SELECT AnalysisPassed FROM AnalysisState WHERE AnalysisName='roofline' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if state is not None and not state["AnalysisPassed"]:
                raise ValueError("Roofline analyzer failed; refusing to render stale data")
        architecture = {
            "id": "arch_1",
            "name": "David " + metadata["Architecture"].upper(),
            "active_cores_count": metadata["ActiveCoreCount"],
            "max_cores_num": metadata["MaxCoreCount"],
            "active_aic_count": metadata["ActiveAicCount"],
            "active_aiv_count": metadata["ActiveAivCount"],
            "reference_frequency_ghz": metadata["ReferenceFrequencyGHz"],
        }
        bandwidth_keys = {
            "SIMD-UB": "peakBandwidthSIMD",
            "SIMT-UB": "peakBandwidthSIMT",
            "SIMT-OUT": "peakBandwidthSIMT_OUT",
            "CUBE-L0": "peakBandwidthL0",
            "CUBE-OUT": "peakBandwidthCUBE_OUT",
        }
        label_map = {
            "SIMD-UB": "SIMD",
            "SIMT-UB": "SIMT",
            "SIMT-OUT": "SIMT-OUT",
            "CUBE-L0": "L0",
            "CUBE-OUT": "CUBE-OUT",
        }
        bandwidths = {}
        for row in database_handler.execute(
            "SELECT * FROM RooflineBandwidthPeaks ORDER BY Unit, MemoryLevel"
        ).fetchall():
            bandwidths.setdefault(row["Unit"], {})[bandwidth_keys[row["MemoryLevel"]]] = row["PeakGBs"]
        for row in database_handler.execute("SELECT * FROM RooflineComputePeaks ORDER BY Unit, DataType").fetchall():
            prefix = "vector_" if row["Unit"] == "AIV" else "cube_"
            architecture.setdefault(row["Unit"], {})[prefix + row["DataType"]] = dict(
                bandwidths.get(row["Unit"], {}), peakFlops=row["PeakGFlops"]
            )
        points = {}
        for row in database_handler.execute("""
            SELECT FunctionName, FunctionPC AS pc, FunctionSize AS func_size,
                   Unit AS unit, MemoryLevel AS label, ExecutionCount AS exec_count,
                   DurationTicks AS duration_sum, TotalOperations AS proc_elem_sum,
                   IntegerOperations AS int_elem_sum, ProcessedBytes AS proc_bytes_sum,
                   ArithmeticIntensity AS arithmetic_intensity, ThroughputGBs AS throughput,
                   PerformanceGFlops AS performance, FpOperations AS fp_ops, IntegerOperations AS integer_ops
            FROM RooflinePoints ORDER BY FunctionPC, ExecutionMode, MemoryLevel
        """).fetchall():
            point = dict(row)
            name = point.pop("FunctionName")
            point["label"] = label_map[point["label"]]
            points[name + " - " + point["label"]] = point
        return {"architecture": architecture, "kernels": points}

    def prepare_report(self, output_dir):
        try:
            data = self.collect()
            output = Path(output_dir)
            (output / "roofline.json").write_text(json.dumps(data, indent=2, allow_nan=False), encoding="utf-8")
            if not data["kernels"]:
                log.warning("Roofline contains no valid points; rerun analysis with DEBUG logging for diagnostics")
            return 0
        except (ValueError, OSError) as error:
            log.error("Roofline failed: %s", error)
            return 1
