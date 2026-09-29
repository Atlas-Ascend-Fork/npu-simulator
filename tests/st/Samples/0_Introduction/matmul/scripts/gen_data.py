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

import os
import sys

os.environ["TORCH_DEVICE_BACKEND_AUTOLOAD"] = "0"

import numpy as np
import torch

# If calculations use float16, please change this to torch.float16 here
DATA_TYPE = torch.bfloat16


def write_artifacts(base_dir, a_data, b_data, out):
    input_dir = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "output")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    a_data.view(torch.uint16).numpy().tofile(os.path.join(input_dir, "input_a.bin"))
    b_data.view(torch.uint16).numpy().tofile(os.path.join(input_dir, "input_b.bin"))
    out.view(torch.uint16).numpy().tofile(os.path.join(output_dir, "cpu_output.bin"))


def gen_golden_data_simple(m, k, n):
    M = m
    K = k
    N = n

    a_ori = np.random.uniform(1, 8, (M, K)).astype(np.float32)
    b_ori = np.random.uniform(1, 8, (K, N)).astype(np.float32)

    a_cpu = torch.from_numpy(a_ori).to(DATA_TYPE)
    b_cpu = torch.from_numpy(b_ori).to(DATA_TYPE)

    out = torch.matmul(a_cpu, b_cpu).to(DATA_TYPE)

    current_dir = os.getcwd()
    write_artifacts(current_dir, a_cpu, b_cpu, out)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.normcase(os.path.abspath(script_dir)) != os.path.normcase(os.path.abspath(current_dir)):
        write_artifacts(script_dir, a_cpu, b_cpu, out)

    print("Data generated successfully!")


if __name__ == "__main__":
    if len(sys.argv) != 4 and len(sys.argv) != 6:
        print("Usage: python3 gen_data.py m k n")
        print("Or")
        print("Usage: python3 gen_data.py m k n transA transB")
        print("Example1: python3 gen_data.py 100 50 200")
        print("Example2: python3 gen_data.py 100 50 200 false true")
        sys.exit(1)

    m = int(sys.argv[1])
    k = int(sys.argv[2])
    n = int(sys.argv[3])

    gen_golden_data_simple(m, k, n)