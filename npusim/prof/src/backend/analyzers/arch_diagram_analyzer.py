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

import logging as log

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple
from collections import defaultdict

from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.common.biprof_enum import CoreType, InstrQueueType, UbAccessType
from npusim.prof.src.common.biprof_constants import BYTES_IN_GB
from npusim.prof.src.backend.analyzers.david.fixp_analyzer import (
    AIV0_UB_MASK,
    AIV1_UB_MASK,
)


class ArchDiagramAnalyzer(IAnalyzer):
    # Architecture diagram data aggregator: computes per-unit throughput and
    # utilization metrics for visualization in the arch diagram report.
    # Collects instruction counts, execution times, and data transfer volumes
    # for each compute unit (CUBE, MTE1-3, FIXP, PUSHQ) and memory hierarchy
    # level (UB, L0A/B/C, L1, OUT). Computes bandwidth (GB/s) and utilization
    # ratios against theoretical peak performance.
    @dataclass
    class Metric:
        gbs: float = 0.0
        cnt: int = 0
        ratio: float = 0.0

        def __add__(self, o):
            return ArchDiagramAnalyzer.Metric(self.gbs + o.gbs, self.cnt + o.cnt, self.ratio + o.ratio)

    TPUT_METRICS: Dict[str, Metric] = defaultdict(Metric)

    OTHER_METRICS: Dict[str, float] = defaultdict(float)

    def __init__(self) -> None:
        super().__init__()
        self._active_cores = self.get_num_active_cores()
        self._exec_time_seconds = self.get_kernel_exec_time_seconds()
        self._multiplier = 1 / self._exec_time_seconds

    @staticmethod
    def get_name() -> str:
        return "arch_diagram"

    def get_num_active_cores(self) -> int:
        core_nums = database_handler.execute("""
        SELECT DISTINCT ExecInstrCoreId
        FROM ExecutedInstructions
        """).fetchall()
        return len(list(core_nums))

    def get_kernel_exec_time_seconds(self):
        exec_duration_ticks = database_handler.get_exec_time_ticks()
        aicore_freq_hz = database_handler.get_frequency_ghz() * 10**9
        exec_duration_sec = (1 / aicore_freq_hz) * exec_duration_ticks
        return exec_duration_sec

    def convert_raw_to_gbs(self, raw_metric):
        return raw_metric / self._active_cores * self._multiplier / BYTES_IN_GB

    def convert_raw_to_gb_tick(self, raw_metric):
        aicore_freq_hz = database_handler.get_frequency_ghz() * 10**9
        return raw_metric / self._active_cores / BYTES_IN_GB * aicore_freq_hz

    @staticmethod
    def calc_throughput_stats(instrs: List[Any], pb_key: str = "ProcessedBytes") -> Tuple[int, int, float]:
        tput_sum = 0
        cnt = 0
        total_exec_time = 0

        cur_time_lb = 0
        cur_time_rb = 0

        for instr in instrs:
            if instr[pb_key] is None:
                continue
            tput_sum += instr[pb_key]
            cnt += 1

            if instr["TickStart"] >= cur_time_rb:
                total_exec_time += cur_time_rb - cur_time_lb
                cur_time_lb = instr["TickStart"]
                cur_time_rb = instr["TickEnd"]
            else:
                cur_time_lb = min(cur_time_lb, instr["TickStart"])
                cur_time_rb = max(cur_time_rb, instr["TickEnd"])
        total_exec_time += cur_time_rb - cur_time_lb
        per_instr_tput = 0 if cnt == 0 else tput_sum / total_exec_time / cnt

        return (tput_sum, cnt, per_instr_tput)

    def get_dma_throughput(self, instrs, src_mem, dst_mem, core_type_id):
        instr_list = str(tuple(instrs)).replace(",)", ")")
        raw_instrs = database_handler.execute(
            """
            SELECT PB.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM DmaMovProcessedBytes AS PB
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            EI.ExecInstrName IN {}
            AND
            json_extract(EI.ExtendParams, '$.src_mem') = :src_mem
            AND
            json_extract(EI.ExtendParams, '$.dst_mem') LIKE :dst_mem
            AND
            EI.CoreTypeId=:core_type_id
            AND
            EI.ExecInstrId=PB.ExecInstrId
            ORDER BY TickEnd
            """.format(instr_list),
            {"src_mem": src_mem, "dst_mem": dst_mem, "core_type_id": core_type_id},
        ).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_ub_throughput(self, core_type_id, ub_access_type, simt_data_cache=False):
        raw_instrs = database_handler.execute(
            """
            SELECT UBS.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM UbRwStatistics AS UBS
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            UBS.ExecInstrId = EI.ExecInstrId
            AND
            EI.CoreTypeId=:core_type_id
            AND
            UBS.UbAccessType=:ub_access_type
            AND
            UBS.SimtDataCache = :simt_data_cache
            ORDER BY TickEnd
            """,
            {
                "core_type_id": core_type_id,
                "ub_access_type": ub_access_type,
                "simt_data_cache": simt_data_cache,
            },
        ).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_mmad_throughput(self):
        raw_instrs = database_handler.execute("""
            SELECT MP.MatrixAProcessedBytes AS PB_A,
                   MP.MatrixBProcessedBytes AS PB_B,
                   MP.MatrixCProcessedBytes AS PB_C,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM MmadParams AS MP
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            MP.ExecInstrId = EI.ExecInstrId
            ORDER BY TickEnd
            """).fetchall()
        sum_a, cnt_a, tp_a = self.calc_throughput_stats(raw_instrs, "PB_A")
        sum_b, cnt_b, tp_b = self.calc_throughput_stats(raw_instrs, "PB_B")
        sum_c, cnt_c, tp_c = self.calc_throughput_stats(raw_instrs, "PB_C")

        _throughput = (sum_a, cnt_a, tp_a, sum_b, cnt_b, tp_b, sum_c, cnt_c, tp_c)
        return _throughput

    def get_l0c_read_throughput(self):
        raw_instrs = database_handler.execute("""
            SELECT MP.MatrixCProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM MmadParams AS MP
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            MP.ExecInstrId = EI.ExecInstrId
            AND
            MP.MatrixCInBiasTableBuffer = 0
            AND
            MP.MatrixCInitVal = 0
            ORDER BY TickEnd
            """).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_fixp_throughput_l1_out(self, src_mem, dst_mem):
        raw_instrs = database_handler.execute(
            """
            SELECT FixpParams.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM FixpParams
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            EI.ExecInstrId=FixpParams.ExecInstrId
            AND
            json_extract(EI.ExtendParams, '$.src_mem') = :src_mem
            AND
            json_extract(EI.ExtendParams, '$.dst_mem') LIKE :dst_mem
            ORDER BY TickEnd
            """,
            {"src_mem": src_mem, "dst_mem": dst_mem},
        ).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_fixp_throughput_ub(self, src_mem, dst_mem, ub_mask):
        rows = database_handler.execute(
            """
            SELECT FixpParams.ProcessedBytes,
                FixpParams.DstUBMask,
                EI.ExecInstrTickEnd,
                EI.ExecInstrTickStart
            FROM FixpParams
            LEFT JOIN ExecutedInstructions as EI
            WHERE
            EI.ExecInstrId=FixpParams.ExecInstrId
            AND
            json_extract(EI.ExtendParams, '$.src_mem') = :src_mem
            AND
            json_extract(EI.ExtendParams, '$.dst_mem') LIKE :dst_mem
            AND
            FixpParams.DstUBMask & :ub_mask
            ORDER BY EI.ExecInstrTickEnd
            """,
            {"src_mem": src_mem, "dst_mem": dst_mem, "ub_mask": ub_mask},
        ).fetchall()
        total_processed_bytes = 0
        total_exec_time = 0
        cur_time_lb = 0
        cur_time_rb = 0
        for row in rows:
            instr_processed_bytes = row[0]
            instr_ub_mask = row[1]
            instr_tick_end = row[2]
            instr_tick_start = row[3]
            if instr_ub_mask == AIV0_UB_MASK | AIV1_UB_MASK:
                instr_processed_bytes /= 2
            total_processed_bytes += instr_processed_bytes
            if instr_tick_start >= cur_time_rb:
                total_exec_time += cur_time_rb - cur_time_lb
                cur_time_lb = instr_tick_start
                cur_time_rb = instr_tick_end
            else:
                cur_time_lb = min(cur_time_lb, instr_tick_start)
                cur_time_rb = max(cur_time_rb, instr_tick_end)
        total_exec_time += cur_time_rb - cur_time_lb
        avg_processed_bytes = 0 if len(rows) == 0 else total_processed_bytes / total_exec_time / len(rows)
        return (total_processed_bytes, len(rows), avg_processed_bytes)

    def get_simt_out_throughput(self, simt_instr_name: str, core_type_id: int):
        raw_instrs = database_handler.execute(
            """
            SELECT PB.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM SIMTLdStProcessedBytes AS PB
            LEFT JOIN ExecutedInstructions AS EI
            WHERE
            EI.ExecInstrId=PB.ExecInstrId
            AND
            PB.ExecInstrName=:simt_instr_name
            AND
            PB.CoreTypeId=:core_type_id
            ORDER BY TickEnd
            """,
            {"core_type_id": core_type_id, "simt_instr_name": simt_instr_name},
        ).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_l2_throughput(self, instrs, cache_hit):
        instr_list = str(tuple(instrs)).replace(",)", ")")
        raw_instrs = database_handler.execute(
            """
            SELECT PB.ProcessedBytes AS ProcessedBytes,
                   EI.ExecInstrTickStart AS TickStart,
                   EI.ExecInstrTickEnd AS TickEnd
            FROM DmaMovProcessedBytes AS PB
            LEFT JOIN ExecutedInstructions AS EI
            ON EI.ExecInstrId=PB.ExecInstrId
            WHERE
            EI.ExecInstrName IN {}
            AND
            json_extract(EI.ExtendParams, '$.src_mem') = 'OUT'
            AND
            PB.CacheHit=:cache_hit
            ORDER BY TickEnd
            """.format(instr_list),
            {"cache_hit": cache_hit},
        ).fetchall()
        return self.calc_throughput_stats(raw_instrs)

    def get_memory_throughput_cube(self) -> None:
        out_to_l1_instrs = [
            "MOV_SRC_TO_DST_ALIGNv2",
            "MOV_OUT_TO_L1_MULTI_ND2NZ",
            "MOV_OUT_TO_L1_MULTI_DN2NZ",
        ]
        aic_out_to_l1_raw = self.get_dma_throughput(out_to_l1_instrs, "OUT", "L1%", CoreType.AIC.value)
        l1_to_l0x_instrs = ["LOAD_2Dv2", "LOAD_L1_TO_DST_3DV2", "LOAD_MX_2Dv2"]
        aic_l1_to_l0a_raw = self.get_dma_throughput(l1_to_l0x_instrs, "L1", "L0A%", CoreType.AIC.value)
        aic_l1_to_l0b_raw = self.get_dma_throughput(l1_to_l0x_instrs, "L1", "L0B%", CoreType.AIC.value)
        self.TPUT_METRICS["aic_out_to_l1"].gbs = self.convert_raw_to_gbs(aic_out_to_l1_raw[0])
        self.TPUT_METRICS["aic_l1_to_l0a"].gbs = self.convert_raw_to_gbs(aic_l1_to_l0a_raw[0])
        self.TPUT_METRICS["aic_l1_to_l0b"].gbs = self.convert_raw_to_gbs(aic_l1_to_l0b_raw[0])

        self.TPUT_METRICS["aic_out_to_l1"].cnt = aic_out_to_l1_raw[1]
        self.TPUT_METRICS["aic_l1_to_l0a"].cnt = aic_l1_to_l0a_raw[1]
        self.TPUT_METRICS["aic_l1_to_l0b"].cnt = aic_l1_to_l0b_raw[1]

        self.TPUT_METRICS["aic_out_to_l1"].ratio = self.convert_raw_to_gb_tick(aic_out_to_l1_raw[2])
        self.TPUT_METRICS["aic_l1_to_l0a"].ratio = self.convert_raw_to_gb_tick(aic_l1_to_l0a_raw[2])
        self.TPUT_METRICS["aic_l1_to_l0b"].ratio = self.convert_raw_to_gb_tick(aic_l1_to_l0b_raw[2])

        mmad_tput_raw = self.get_mmad_throughput()
        self.TPUT_METRICS["aic_l0a_to_cube"].gbs = self.convert_raw_to_gbs(mmad_tput_raw[0])
        self.TPUT_METRICS["aic_l0b_to_cube"].gbs = self.convert_raw_to_gbs(mmad_tput_raw[3])
        self.TPUT_METRICS["aic_cube_to_l0c"].gbs = self.convert_raw_to_gbs(mmad_tput_raw[6])
        self.TPUT_METRICS["aic_l0a_to_cube"].cnt = mmad_tput_raw[1]
        self.TPUT_METRICS["aic_l0b_to_cube"].cnt = mmad_tput_raw[4]
        self.TPUT_METRICS["aic_cube_to_l0c"].cnt = mmad_tput_raw[7]
        self.TPUT_METRICS["aic_l0a_to_cube"].ratio = self.convert_raw_to_gb_tick(mmad_tput_raw[2])
        self.TPUT_METRICS["aic_l0b_to_cube"].ratio = self.convert_raw_to_gb_tick(mmad_tput_raw[5])
        self.TPUT_METRICS["aic_cube_to_l0c"].ratio = self.convert_raw_to_gb_tick(mmad_tput_raw[8])

        aic_l0c_to_out_raw = self.get_fixp_throughput_l1_out("L0C", "OUT%")
        aic_l0c_to_l1_raw = self.get_fixp_throughput_l1_out("L0C", "L1%")
        aic_l0c_to_ub0_raw = self.get_fixp_throughput_ub("L0C", "UB%", AIV0_UB_MASK)
        aic_l0c_to_ub1_raw = self.get_fixp_throughput_ub("L0C", "UB%", AIV1_UB_MASK)
        aic_l0c_to_cube_raw = self.get_l0c_read_throughput()

        self.TPUT_METRICS["aic_l0c_to_out"].gbs = self.convert_raw_to_gbs(aic_l0c_to_out_raw[0])
        self.TPUT_METRICS["aic_l0c_to_l1"].gbs = self.convert_raw_to_gbs(aic_l0c_to_l1_raw[0])
        self.TPUT_METRICS["aic_l0c_to_ub0"].gbs = self.convert_raw_to_gbs(aic_l0c_to_ub0_raw[0])
        self.TPUT_METRICS["aic_l0c_to_ub1"].gbs = self.convert_raw_to_gbs(aic_l0c_to_ub1_raw[0])
        self.TPUT_METRICS["aic_l0c_to_cube"].gbs = self.convert_raw_to_gbs(aic_l0c_to_cube_raw[0])

        self.TPUT_METRICS["aic_l0c_to_out"].cnt = aic_l0c_to_out_raw[1]
        self.TPUT_METRICS["aic_l0c_to_l1"].cnt = aic_l0c_to_l1_raw[1]
        self.TPUT_METRICS["aic_l0c_to_ub0"].cnt = aic_l0c_to_ub0_raw[1]
        self.TPUT_METRICS["aic_l0c_to_ub1"].cnt = aic_l0c_to_ub1_raw[1]
        self.TPUT_METRICS["aic_l0c_to_cube"].cnt = aic_l0c_to_cube_raw[1]

        self.TPUT_METRICS["aic_l0c_to_out"].ratio = self.convert_raw_to_gb_tick(aic_l0c_to_out_raw[2])
        self.TPUT_METRICS["aic_l0c_to_l1"].ratio = self.convert_raw_to_gb_tick(aic_l0c_to_l1_raw[2])
        self.TPUT_METRICS["aic_l0c_to_ub0"].ratio = self.convert_raw_to_gb_tick(aic_l0c_to_ub0_raw[2])
        self.TPUT_METRICS["aic_l0c_to_ub1"].ratio = self.convert_raw_to_gb_tick(aic_l0c_to_ub1_raw[2])
        self.TPUT_METRICS["aic_l0c_to_cube"].ratio = self.convert_raw_to_gb_tick(aic_l0c_to_cube_raw[2])

    def get_memory_throughput_vector(self, core: CoreType) -> None:
        core_str = core.name.lower()
        out_to_ub_instrs = ["MOV_SRC_TO_DST_ALIGNv2", "ND_DMA"]
        ub_to_out_instrs = out_to_ub_instrs
        ub_to_l1_instrs = ["MOV_UB_TO_L1"]

        ub_to_simd_raw = self.get_ub_throughput(core.value, UbAccessType.SIMD_RD.value)
        simd_to_ub_raw = self.get_ub_throughput(core.value, UbAccessType.SIMD_WR.value)
        ub_to_simt_raw = self.get_ub_throughput(core.value, UbAccessType.SIMT_RD.value)
        simt_to_ub_raw = self.get_ub_throughput(core.value, UbAccessType.SIMT_WR.value)
        simt_to_cache_raw = self.get_ub_throughput(core.value, UbAccessType.SIMT_WR.value, True)
        cache_to_simt_raw = self.get_ub_throughput(core.value, UbAccessType.SIMT_RD.value, True)
        out_to_ub_raw = self.get_dma_throughput(out_to_ub_instrs, "OUT", "UB%", core.value)
        ub_to_out_raw = self.get_dma_throughput(ub_to_out_instrs, "UB", "OUT%", core.value)
        ub_to_l1_raw = self.get_dma_throughput(ub_to_l1_instrs, "UB", "L1%", core.value)
        out_to_simt_raw = self.get_simt_out_throughput("SIMT_LDG", core.value)
        simt_to_out_raw = self.get_simt_out_throughput("SIMT_STG", core.value)

        self.TPUT_METRICS[f"{core_str}_ub_to_simd"].gbs = self.convert_raw_to_gbs(ub_to_simd_raw[0])
        self.TPUT_METRICS[f"{core_str}_simd_to_ub"].gbs = self.convert_raw_to_gbs(simd_to_ub_raw[0])
        self.TPUT_METRICS[f"{core_str}_ub_to_simt"].gbs = self.convert_raw_to_gbs(ub_to_simt_raw[0])
        self.TPUT_METRICS[f"{core_str}_simt_to_ub"].gbs = self.convert_raw_to_gbs(simt_to_ub_raw[0])
        self.TPUT_METRICS[f"{core_str}_cache_to_simt"].gbs = self.convert_raw_to_gbs(cache_to_simt_raw[0])
        self.TPUT_METRICS[f"{core_str}_simt_to_cache"].gbs = self.convert_raw_to_gbs(simt_to_cache_raw[0])

        self.TPUT_METRICS[f"{core_str}_ub_to_simd"].cnt = ub_to_simd_raw[1]
        self.TPUT_METRICS[f"{core_str}_simd_to_ub"].cnt = simd_to_ub_raw[1]
        self.TPUT_METRICS[f"{core_str}_ub_to_simt"].cnt = ub_to_simt_raw[1]
        self.TPUT_METRICS[f"{core_str}_simt_to_ub"].cnt = simt_to_ub_raw[1]
        self.TPUT_METRICS[f"{core_str}_cache_to_simt"].cnt = cache_to_simt_raw[1]
        self.TPUT_METRICS[f"{core_str}_simt_to_cache"].cnt = simt_to_cache_raw[1]

        self.TPUT_METRICS[f"{core_str}_ub_to_simd"].ratio = self.convert_raw_to_gb_tick(ub_to_simd_raw[2])
        self.TPUT_METRICS[f"{core_str}_simd_to_ub"].ratio = self.convert_raw_to_gb_tick(simd_to_ub_raw[2])
        self.TPUT_METRICS[f"{core_str}_ub_to_simt"].ratio = self.convert_raw_to_gb_tick(ub_to_simt_raw[2])
        self.TPUT_METRICS[f"{core_str}_simt_to_ub"].ratio = self.convert_raw_to_gb_tick(simt_to_ub_raw[2])
        self.TPUT_METRICS[f"{core_str}_cache_to_simt"].ratio = self.convert_raw_to_gb_tick(cache_to_simt_raw[2])
        self.TPUT_METRICS[f"{core_str}_simt_to_cache"].ratio = self.convert_raw_to_gb_tick(simt_to_cache_raw[2])

        self.TPUT_METRICS[f"{core_str}_out_to_ub"].gbs = self.convert_raw_to_gbs(out_to_ub_raw[0])
        self.TPUT_METRICS[f"{core_str}_ub_to_out"].gbs = self.convert_raw_to_gbs(ub_to_out_raw[0])
        self.TPUT_METRICS[f"{core_str}_ub_to_l1"].gbs = self.convert_raw_to_gbs(ub_to_l1_raw[0])
        self.TPUT_METRICS[f"{core_str}_out_to_simt"].gbs = self.convert_raw_to_gbs(out_to_simt_raw[0])
        self.TPUT_METRICS[f"{core_str}_simt_to_out"].gbs = self.convert_raw_to_gbs(simt_to_out_raw[0])

        self.TPUT_METRICS[f"{core_str}_out_to_ub"].cnt = out_to_ub_raw[1]
        self.TPUT_METRICS[f"{core_str}_ub_to_out"].cnt = ub_to_out_raw[1]
        self.TPUT_METRICS[f"{core_str}_ub_to_l1"].cnt = ub_to_l1_raw[1]
        self.TPUT_METRICS[f"{core_str}_out_to_simt"].cnt = out_to_simt_raw[1]
        self.TPUT_METRICS[f"{core_str}_simt_to_out"].cnt = simt_to_out_raw[1]

        self.TPUT_METRICS[f"{core_str}_out_to_ub"].ratio = self.convert_raw_to_gb_tick(out_to_ub_raw[2])
        self.TPUT_METRICS[f"{core_str}_ub_to_out"].ratio = self.convert_raw_to_gb_tick(ub_to_out_raw[2])
        self.TPUT_METRICS[f"{core_str}_ub_to_l1"].ratio = self.convert_raw_to_gb_tick(ub_to_l1_raw[2])
        self.TPUT_METRICS[f"{core_str}_out_to_simt"].ratio = self.convert_raw_to_gb_tick(out_to_simt_raw[2])
        self.TPUT_METRICS[f"{core_str}_simt_to_out"].ratio = self.convert_raw_to_gb_tick(simt_to_out_raw[2])

    def get_memory_throughput_l2(self) -> None:
        out_instrs = [
            "MOV_SRC_TO_DST_ALIGNv2",
            "MOV_OUT_TO_L1_MULTI_ND2NZ",
            "MOV_OUT_TO_L1_MULTI_DN2NZ",
        ]
        uncached_raw = self.get_l2_throughput(out_instrs, False)
        cached_raw = self.get_l2_throughput(out_instrs, True)
        self.TPUT_METRICS["hbm_to_l2_syn"].gbs = self.convert_raw_to_gbs(uncached_raw[0])
        self.TPUT_METRICS["hbm_to_l2_syn"].cnt = uncached_raw[1]
        self.TPUT_METRICS["hbm_to_l2_syn"].ratio = self.convert_raw_to_gb_tick(uncached_raw[2])

        self.OTHER_METRICS["l2_cached_ratio"] = 0
        if uncached_raw[0] + cached_raw[0] != 0:
            self.OTHER_METRICS["l2_cached_ratio"] = 100 * cached_raw[0] / (uncached_raw[0] + cached_raw[0])

    def get_memory_throughput_synthetic(self) -> None:
        self.TPUT_METRICS["aic_l0c_to_all_syn"] = (
            self.TPUT_METRICS["aic_l0c_to_out"]
            + self.TPUT_METRICS["aic_l0c_to_l1"]
            + self.TPUT_METRICS["aic_l0c_to_ub0"]
            + self.TPUT_METRICS["aic_l0c_to_ub1"]
        )

        self.TPUT_METRICS["l2_to_hbm_syn"] = (
            self.TPUT_METRICS["aic_l0c_to_out"]
            + self.TPUT_METRICS["aiv0_ub_to_out"]
            + self.TPUT_METRICS["aiv0_simt_to_out"]
            + self.TPUT_METRICS["aiv1_ub_to_out"]
            + self.TPUT_METRICS["aiv1_simt_to_out"]
        )

    def get_memory_throughput(self) -> None:
        self.get_memory_throughput_cube()
        self.get_memory_throughput_vector(CoreType.AIV0)
        self.get_memory_throughput_vector(CoreType.AIV1)
        self.get_memory_throughput_synthetic()
        self.get_memory_throughput_l2()

    def get_pipe_utilization_data(self, coreTypeId, instrTypeId):
        counter = database_handler.execute(
            """
            SELECT AVG(PipeUtilization)
            FROM PipesUtilization
            WHERE
            CoreTypeId=:coreTypeId
            AND
            InstrQueueTypeId=:instrTypeId
            """,
            {"coreTypeId": coreTypeId, "instrTypeId": instrTypeId},
        ).fetchone()[0]
        if counter is None:
            counter = 0
        return counter

    def get_pipe_utilization(self):
        self.OTHER_METRICS["aiv0_simd_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV0.value, InstrQueueType.SIMD.value
        )
        self.OTHER_METRICS["aiv0_simt_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV0.value, InstrQueueType.SIMT.value
        )
        self.OTHER_METRICS["aiv0_scalar_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV0.value, InstrQueueType.SCALAR.value
        )
        self.OTHER_METRICS["aiv1_simd_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV1.value, InstrQueueType.SIMD.value
        )
        self.OTHER_METRICS["aiv1_simt_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV1.value, InstrQueueType.SIMT.value
        )
        self.OTHER_METRICS["aiv1_scalar_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIV1.value, InstrQueueType.SCALAR.value
        )
        self.OTHER_METRICS["aic_cube_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIC.value, InstrQueueType.CUBE.value
        )
        self.OTHER_METRICS["aic_fixp_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIC.value, InstrQueueType.FIXP.value
        )
        self.OTHER_METRICS["aic_scalar_ratio"] = self.get_pipe_utilization_data(
            CoreType.AIC.value, InstrQueueType.SCALAR.value
        )

    def get_basic_metrics(self):
        self.OTHER_METRICS["active_cores"] = self._active_cores
        self.OTHER_METRICS["exec_time_secs"] = self._exec_time_seconds

    def store_metrics_to_db(self):
        for n, d in self.TPUT_METRICS.items():
            log.debug(f"{n} = (gbs: {d.gbs}, req: {d.cnt}, ratio: {d.ratio})")
        for n, d in self.OTHER_METRICS.items():
            log.debug(f"{n} = {d}")

        all_metrics = list(self.OTHER_METRICS.items())
        for name, data in self.TPUT_METRICS.items():
            all_metrics.extend(
                [
                    (name + "_gbs", data.gbs),
                    (name + "_cnt", data.cnt),
                    (name + "_ratio", data.ratio),
                ]
            )

        database_handler.executemany(
            """
            INSERT INTO ArchDiagramMetrics
            (ArchDiagramParameterName, ArchDiagramParameterValue)
            VALUES (?, ?)
            """,
            all_metrics,
        )

    def process(self) -> None:
        self.get_basic_metrics()
        self.get_memory_throughput()
        self.get_pipe_utilization()
        self.store_metrics_to_db()

    def cleanup(self) -> None:
        self.TPUT_METRICS.clear()
        self.OTHER_METRICS.clear()
