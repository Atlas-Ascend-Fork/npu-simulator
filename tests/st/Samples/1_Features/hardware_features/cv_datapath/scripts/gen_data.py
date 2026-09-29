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

"""Generate MatMul+ReLU inputs and golden for cv_datapath."""

import os
import sys
import numpy as np

np.random.seed(9)

# Must match include/kernel_common.h (Cube params gelu-aligned; singleCore sized for 28-core SKU)
M = 256
K = 256
N = 256
BASE_M = 256
BASE_N = 256
BASE_K = 64
SINGLE_CORE_M = 256
SINGLE_CORE_N = 256
SINGLE_CORE_K = 256


def _check_tiling_divisible():
    if M % SINGLE_CORE_M != 0 or N % SINGLE_CORE_N != 0 or K % SINGLE_CORE_K != 0:
        raise ValueError(
            f"M/N/K must be multiples of singleCore: "
            f"M={M}, N={N}, K={K}, singleCore=({SINGLE_CORE_M},{SINGLE_CORE_N},{SINGLE_CORE_K})"
        )
    if SINGLE_CORE_M % BASE_M != 0 or SINGLE_CORE_N % BASE_N != 0 or SINGLE_CORE_K % BASE_K != 0:
        raise ValueError(
            f"singleCore must be multiples of base: "
            f"singleCore=({SINGLE_CORE_M},{SINGLE_CORE_N},{SINGLE_CORE_K}), "
            f"base=({BASE_M},{BASE_N},{BASE_K})"
        )


def gen_golden_data():
    _check_tiling_divisible()

    input_type = np.dtype("float16")
    output_type = np.dtype("float32")

    x1_gm = np.random.uniform(-1, 1, [M, K]).astype(input_type)
    x2_gm = np.random.uniform(-1, 1, [K, N]).astype(input_type)

    golden = np.matmul(x1_gm.astype(np.float32), x2_gm.astype(np.float32)).astype(np.float32)
    golden = np.maximum(golden, 0.0).astype(output_type)

    os.makedirs("input", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    x1_gm.astype(input_type).tofile("./input/x1_gm.bin")
    # Match matmul_gelu_high_performance: store B as [N,K] for IS_B_TRANSPOSE Cube path.
    x2_gm.transpose().astype(input_type).tofile("./input/x2_gm.bin")
    golden.astype(output_type).tofile("./output/golden.bin")
    print(
        f"Generated data: M={M}, K={K}, N={N}, "
        f"base=({BASE_M},{BASE_N},{BASE_K}), "
        f"singleCore=({SINGLE_CORE_M},{SINGLE_CORE_N},{SINGLE_CORE_K}), "
        f"B_layout=transposed[N,K], elements={M * N}"
    )


if __name__ == "__main__":
    try:
        gen_golden_data()
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)