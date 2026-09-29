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
import numpy as np
import en_dtypes


def gen_golden_data_simple():
    input_shape_x = [1, 2048]
    input_shape_scale = [1, 2048]
    dtype = np.float32
    y_dtype = en_dtypes.hifloat8
    input_x = np.random.uniform(-10, 10, input_shape_x).astype(dtype)
    input_scale = np.random.uniform(-10, 10, input_shape_scale).astype(dtype)
    input_offset = np.random.uniform(-10, 10, input_shape_scale).astype(dtype)
    y_golden = (input_x / input_scale + input_offset).astype(y_dtype)
    os.makedirs("input", exist_ok=True)
    os.makedirs("output", exist_ok=True)
    input_x.tofile("./input/input_x.bin")
    input_scale.tofile("./input/input_scale.bin")
    input_offset.tofile("./input/input_offset.bin")
    y_golden.tofile("./output/golden_y.bin")


if __name__ == "__main__":
    gen_golden_data_simple()