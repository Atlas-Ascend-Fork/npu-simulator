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

import sys
import numpy as np
import en_dtypes

ERROR_TOL = 1e-3
data_type = en_dtypes.hifloat8


def verify_result(output, golden):
    # 1ulp对比方式
    output = np.fromfile(output, dtype=data_type).view(np.int8)
    golden = np.fromfile(golden, dtype=data_type).view(np.int8)
    diff_results = np.abs(np.subtract(output, golden))
    diff_indices = np.where(diff_results > 1)[0]

    npu_nan, golden_nan = np.isnan(output), np.isnan(golden)
    diff_nan = np.logical_and(npu_nan, golden_nan)
    both_nan_idx = np.where(diff_nan)
    diff_indices = np.setdiff1d(diff_indices, both_nan_idx)

    for index, real_index in enumerate(diff_indices):
        golden_data = golden[real_index]
        output_data = output[real_index]
        print(
            "data index: %06d, expected: %-.9f, actual: %-.9f, rdiff: %-.6f"
            % (real_index, golden_data, output_data, abs(output_data - golden_data) / golden_data)
        )
        if index == 100:
            break
    print("golden_data : ", golden)
    print("output : ", output)
    error_ratio = float(diff_indices.size) / golden.size
    print("error ratio: %.4f, tolerance: %.4f" % (error_ratio, ERROR_TOL))
    return error_ratio <= ERROR_TOL


if __name__ == "__main__":
    try:
        v_result = verify_result(sys.argv[1], sys.argv[2])
        if not v_result:
            raise ValueError("[ERROR] result error!!!")
        else:
            print("test pass!!!")
    except Exception as ex:
        print(ex)
        sys.exit(1)