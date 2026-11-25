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
from typing import List, Optional, Tuple

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import (
    dfi_clk_ns_map,
    peak_bandwidth_map,
    peak_cube_mac_per_cycle_map,
    peak_ipc_map,
    V100_DDR_PER_CHANNEL_INTERVAL_BYTES,
)
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.biprof_enum import CoreType, InstrType


class SpeedOfLightAnalyzer(IAnalyzer):
    @staticmethod
    def get_name() -> str:
        return "speed_of_light"

    @staticmethod
    def save_sol_info(info: List[Tuple[str, float]]) -> None:
        database_handler.executemany(
            """
        INSERT OR REPLACE INTO SpeedOfLightData
        VALUES (?, ?)
        """,
            info,
        )

    @staticmethod
    def _get_ddr_sample_count() -> Optional[int]:
        if not database_handler.table_exists("KernelInfo"):
            return None
        row = database_handler.execute(
            """
            SELECT KernelInfoVal
            FROM KernelInfo
            WHERE KernelInfoAttr='ddr_state_sample_count'
            """
        ).fetchone()
        if row is None:
            return None
        try:
            return int(row["KernelInfoVal"])
        except (TypeError, ValueError):
            return None

    def process(self) -> None:
        res: List[Tuple[str, float]] = []
        exec_time = database_handler.get_exec_time_ticks()
        res.append(("CUBE", self.get_cube_throughput(exec_time)))
        res.append(("VECTOR", self.get_vector_throughput(exec_time)))
        self.save_sol_info(res)
        # Separating compute calculations from memory calculation allows
        # compute calculations to be retained if memory calculation fails;
        # a None result (data not collected) is skipped, never stored as 0
        try:
            memory_tp = self.get_memory_throughput()
            if memory_tp is not None:
                self.save_sol_info([("MEMORY", memory_tp)])
        except Exception:
            log.exception("Memory speed of light calculation failed")

    def get_cube_throughput(self, kernel_exec_time: int) -> float:
        # The minimum amount of cycles it would take
        # to execute all MMAD instructions, as per hardware limits
        ideal_mmad_cyc = 0.0

        # Sum total of kernel execution time,
        # adjusted for the number of cores with MMAD work
        kerneltime_across_cores = 0.0

        # Maximum amount of elements of a given size that can be
        # processed per cycle
        mac_map = peak_cube_mac_per_cycle_map[biprof_ctx.arch]
        for core_id in self.get_active_cores():
            # Fetch all MMAD instructions for a given core
            mmad_instrs = database_handler.execute(
                """
            SELECT M, K, N, MatrixADtype
            FROM MmadParams LEFT JOIN ExecutedInstructions
            ON MmadParams.ExecInstrId=ExecutedInstructions.ExecInstrId
            WHERE ExecInstrCoreId=:core_id
            """,
                {"core_id": core_id},
            ).fetchall()
            # Skip the core altogether if no MMAD work was done
            if len(mmad_instrs) == 0:
                continue
            kerneltime_across_cores += kernel_exec_time

            # Count up ideal cycle count based on
            # the number of elements and datatype
            # (dtype of Matrix A is used as the best indicator
            # of the cycles necessary, as Matrix B is always
            # converted into a dtype of a size matching Matrix A)
            for instr in mmad_instrs:
                elem_count = instr["M"] * instr["N"] * instr["K"]
                dtype = instr["MatrixADtype"] or ""
                if not dtype:
                    log.warning("MMAD instruction without MatrixADtype; skipped in cube speed of light")
                    continue

                if "32" in dtype:
                    peak_mac = mac_map["f32"]
                elif "16" in dtype:
                    peak_mac = mac_map["f16"]
                elif "8" in dtype:
                    peak_mac = mac_map["f8"]
                elif "4" in dtype:
                    peak_mac = mac_map["f4"]
                else:
                    log.warning("Unsupported MatrixADtype %r; skipped in cube speed of light", dtype)
                    continue
                ideal_mmad_cyc += elem_count / peak_mac

        # Divide by zero prevention
        if kerneltime_across_cores == 0:
            return 0
        return ideal_mmad_cyc / kerneltime_across_cores * 100

    def get_vector_throughput(self, kernel_exec_time: int) -> float:
        # Find which vector core types (AIV0, AIV1), if any, are active,
        # to reduce unnecessary db queries
        active_core_types = set(self.get_active_core_types())
        aiv_core_types = [ct for ct in (CoreType.AIV0.value, CoreType.AIV1.value) if ct in active_core_types]
        if not aiv_core_types:
            return 0

        # Hardware per-cycle maximums for SIMD and SIMT respectively
        peak_ipc_simd = peak_ipc_map[biprof_ctx.arch]["SIMD"]
        peak_ipc_simt = peak_ipc_map[biprof_ctx.arch]["SIMT"]

        # Sum total of kernel execution time,
        # adjusted for the number of cores with relevant work
        # (compute only for SIMD, any SIMT instructions for SIMT)
        kerneltime_across_cores = 0

        # The minimum amount of cycles it would take
        # to execute all relevant instructions, as per hardware limits
        total_ideal_count = 0
        for core_id in self.get_active_cores():
            for core_type_id in aiv_core_types:
                # Fetch the number of relevant instructions for a given core
                # Only compute instructions are counted for SIMD
                simd_row = database_handler.execute(
                    """
                SELECT COUNT(ExecInstrId) as cnt
                FROM ExecutedInstructions
                WHERE InstrTypeId=:rvecex
                AND ExecInstrName NOT LIKE 'SIMT_%'
                AND ExecInstrCoreId=:core_id
                AND CoreTypeId=:core_type_id
                """,
                    {
                        "rvecex": InstrType.RVECEX.value,
                        "core_id": core_id,
                        "core_type_id": core_type_id,
                    },
                ).fetchone()
                # All SIMT instructions are counted for SIMT, because of
                # the difficulty in determining a compute-only maximum
                simt_row = database_handler.execute(
                    """
                SELECT COUNT(ExecInstrId) as cnt
                FROM ExecutedInstructions
                WHERE ExecInstrName LIKE 'SIMT_%'
                AND ExecInstrCoreId=:core_id
                AND CoreTypeId=:core_type_id
                """,
                    {"core_id": core_id, "core_type_id": core_type_id},
                ).fetchone()

                simd_cnt = simd_row["cnt"]
                simt_cnt = simt_row["cnt"]
                # Skip the core altogether if no relevant work was done
                if simd_cnt + simt_cnt == 0:
                    continue

                kerneltime_across_cores += kernel_exec_time
                # Total up the ideal cycle count,
                # based on hardware maximums
                total_ideal_count += simd_cnt / peak_ipc_simd + simt_cnt / peak_ipc_simt

        # Divide by zero prevention
        if kerneltime_across_cores == 0:
            return 0
        return total_ideal_count / kerneltime_across_cores * 100

    def get_memory_throughput(self) -> Optional[float]:
        # Collection switch off, STATE_TIME==0 or an old helper: the
        # table is empty/absent and memory SOL must stay unavailable
        # (never reported as 0) - see DdrStateSamples protocol §6.6/§7
        sample_count = self._get_ddr_sample_count()
        if not database_handler.table_exists("DdrStateSamples") or not sample_count:
            log.warning("DdrStateSamples is absent or empty; memory speed of light is unavailable")
            return None

        # No clipping to the kernel's instruction tick window: the two
        # tables tick in different clock domains (ExecutedInstructions
        # on the AI core clock, DdrStateSamples on the DFI clock), so
        # their tick values must not be compared. Each contract DB is a
        # single kernel launch and the active-window logic below already
        # trims pre/post-kernel idle time
        # Data self-check: a row above the per-channel interval bound
        # indicates a missing counter reset or an over-long interval
        # (DdrStateSamples protocol §6.1)
        abnormal = database_handler.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM DdrStateSamples
            WHERE DataTotalBytes > :bound
            """,
            {"bound": V100_DDR_PER_CHANNEL_INTERVAL_BYTES},
        ).fetchone()
        if abnormal["cnt"] > 0:
            log.warning(
                "%d DdrStateSamples rows exceed the per-channel interval "
                "bound (%d bytes); abnormal interval span suspected",
                abnormal["cnt"],
                V100_DDR_PER_CHANNEL_INTERVAL_BYTES,
            )

        # One row per interval: bytes summed across all DDR channels and
        # controller instances, duration taken once per interval (all
        # channels share the same timeline - the yellow-zone chart15
        # script sums per-channel bandwidths over it, so summing the
        # per-channel tick spans here would dilute SOL by the channel
        # count). MAX is used because interval boundaries are only
        # guaranteed non-overlapping per channel (DdrStateSamples
        # protocol §6.5)
        rows = database_handler.execute(
            """
            SELECT IntervalEndTick AS end_tick,
                   MAX(IntervalEndTick - IntervalStartTick) AS dur_ticks,
                   SUM(DataTotalBytes) AS total_bytes
            FROM DdrStateSamples
            GROUP BY IntervalEndTick
            ORDER BY IntervalEndTick
            """,
        ).fetchall()
        if not rows:
            log.warning("DdrStateSamples table has no rows; memory speed of light is unavailable")
            return None

        # Active window = first/last interval with traffic,
        # interior zero intervals included (DdrStateSamples protocol §6.2)
        active_idx = [i for i, r in enumerate(rows) if r["total_bytes"] > 0]
        if not active_idx:
            log.warning("No DDR traffic in DdrStateSamples; memory speed of light is unavailable")
            return None
        start = active_idx[0]
        stop = active_idx[-1] + 1
        window = rows[start:stop]

        total_bytes = sum(r["total_bytes"] for r in window)
        # Actual tick span per interval; no constant-interval assumption
        # (dropped samples make intervals a multiple of STATE_TIME)
        dur_ticks = sum(r["dur_ticks"] for r in window)
        dur_s = dur_ticks * dfi_clk_ns_map[biprof_ctx.arch] * 1e-9
        if dur_s <= 0:
            log.warning("Non-positive DDR sampling duration; memory speed of light is unavailable")
            return None

        peak_bw_bps = peak_bandwidth_map[biprof_ctx.arch]["OUT"] * 1e9
        return total_bytes / dur_s / peak_bw_bps * 100.0
