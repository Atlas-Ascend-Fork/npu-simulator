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

"""
典型算子在 CAModel (CPU) 上的仿真耗时参考数据。

测试平台: CPU 64 cores @ 2.800GHz, Memory 256GB
单位:
    - Simulation time(ms): 毫秒 (ms)，CAModel 在 CPU 上仿真单个算子的耗时
    - Cycles: 芯片周期数，对应硬件执行的理论周期数
注意: CAModel 在 CPU 上仿真会比真实硬件慢数十倍，仅供心理预期参考。
"""

TYPICAL_OPERATOR_TIMES = [
    {
        "Operator type": "CV MIX",
        "Case name": "FA",
        "Parameters": "query,key,value shape: {256, 1, 128}",
        "Cycles": 32314,
        "Simulation time(ms)": 93578,
    },
    {
        "Operator type": "CV MIX",
        "Case name": "FAG",
        "Parameters": "query,key,value shape: {256, 1, 128}",
        "Cycles": 41151,
        "Simulation time(ms)": 129973,
    },
    {
        "Operator type": "CV MIX",
        "Case name": "IFA",
        "Parameters": "query shape:{1, 2, 1, 16},key,value shape: {1, 2, 2, 16}",
        "Cycles": 20654,
        "Simulation time(ms)": 62992,
    },
    {
        "Operator type": "CV MIX",
        "Case name": "PFA",
        "Parameters": "query shape:{1, 2, 1, 16},key,value shape: {1, 2, 2, 16}",
        "Cycles": 19093,
        "Simulation time(ms)": 58372,
    },
    {
        "Operator type": "CUBE",
        "Case name": "Matmul(Non-Quantized)",
        "Parameters": "M*N, N*K, M=16,N=32,K=16",
        "Cycles": 7164,
        "Simulation time(ms)": 30680,
    },
    {
        "Operator type": "CUBE",
        "Case name": "Matmul A8W8",
        "Parameters": "M*N, N*K, M=16,N=16,K=16",
        "Cycles": 8533,
        "Simulation time(ms)": 32733,
    },
    {
        "Operator type": "CUBE",
        "Case name": "Matmul A16W8",
        "Parameters": "M*N, N*K, M=16,N=32,K=16",
        "Cycles": 12998,
        "Simulation time(ms)": 40867,
    },
    {
        "Operator type": "VECTOR",
        "Case name": "Softmax(Regbase)",
        "Parameters": "batch_size:4, feature_dim:2",
        "Cycles": 5259,
        "Simulation time(ms)": 24453,
    },
]
