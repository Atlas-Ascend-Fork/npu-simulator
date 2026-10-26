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

from npusim.prof.src.common.biprof_enum import ArchName
from typing import List


ARCH_DIAG_SUPPORTED_ARCH: List[ArchName] = [
    ArchName.V100,
]

# Theoretical bandwidth peaks calculated for 1.65GHz frequency
MEMORY_BANDWIDTH_V100 = {
    # Cube
    "aic_out_to_l1": 196.7,  # GB/s, GM to L1, 128 bpc
    "aic_l0c_to_all_syn": 786.78,  # GB/s, L0C to FIXP, 512 bpc
    "aic_l1_to_l0a": 393.39,  # GB/s, L1 to L0A, 256 bpc
    "aic_l1_to_l0b": 393.39,  # GB/s, L1 to L0B, 256 bpc
    "aic_l0a_to_cube": 786.78,  # GB/s, L0A to Cube, 512 bpc
    "aic_l0b_to_cube": 786.78,  # GB/s, L0B to Cube, 512 bpc
    "aic_l0c_to_cube": 1573.56,  # GB/s, L0C to Cube, 1024 bpc
    "aic_cube_to_l0c": 1573.56,  # GB/s, Cube to L0C, 1024 bpc
    "aic_l0c_to_l1": 196.7,  # GB/s, L0C to L1, 128 bpc
    "aic_l0c_to_out": 196.7,  # GB/s, L0C to GM, 128 bpc
    "aic_l0c_to_ub0": 196.7,  # GB/s, L0C to Vec0 UB, 128 bpc
    "aic_l0c_to_ub1": 196.7,  # GB/s, L0C to Vec1 UB, 128 bpc
    # Vector 0
    "aiv0_out_to_ub": 196.7,  # GB/s, GM to UB, 128 bpc
    "aiv0_ub_to_out": 196.7,  # GB/s, UB to GM, 128 bpc
    "aiv0_out_to_simt": 196.7,  # GB/s, GM to SIMT, 128 bpc
    "aiv0_simt_to_out": 196.7,  # GB/s, SIMT to GM, 128 bpc
    "aiv0_ub_to_simt": 196.7,  # GB/s, UB to SIMT, 128 bpc
    "aiv0_simt_to_ub": 196.7,  # GB/s, SIMT to UB, 128 bpc
    "aiv0_ub_to_simd": 786.78,  # GB/s, UB to SIMD, 512 bpc
    "aiv0_simd_to_ub": 393.39,  # GB/s, SIMD to UB, 256 bpc
    "aiv0_ub_to_l1": 196.7,  # GB/s, UB to L1, 128 bpc
    # Vector 1
    "aiv1_out_to_ub": 196.7,  # GB/s, GM to UB, 128 bpc
    "aiv1_ub_to_out": 196.7,  # GB/s, UB to GM, 128 bpc
    "aiv1_out_to_simt": 196.7,  # GB/s, GM to SIMT, 128 bpc
    "aiv1_simt_to_out": 196.7,  # GB/s, SIMT to GM, 128 bpc
    "aiv1_ub_to_simt": 196.7,  # GB/s, UB to SIMT, 128 bpc
    "aiv1_simt_to_ub": 196.7,  # GB/s, SIMT to UB, 128 bpc
    "aiv1_ub_to_simd": 786.78,  # GB/s, UB to SIMD, 512 bpc
    "aiv1_simd_to_ub": 393.39,  # GB/s, SIMD to UB, 256 bpc
    "aiv1_ub_to_l1": 196.7,  # GB/s, UB to L1, 128 bpc
    # Chip (all cores, total bandwidth for both reads + writes)
    "chip_to_out": 1.6 * 1024,  # GB/s
}
