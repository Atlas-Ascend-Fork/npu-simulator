/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the License);
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an AS IS BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*!
 * \file bw_common.h
 * \brief 带宽测试样例（bw_rcw / bw_read / bw_rw ...）共用的宏与常量。
 */

#ifndef BW_COMMON_H
#define BW_COMMON_H

#include "acl/acl.h"
#include <cstdint>
#include <iostream>

inline bool CheckAcl(aclError err, const char *file, int line) {
  if (err != ACL_SUCCESS) {
    std::cerr << "ACL error: " << err << " at " << file << ":" << line
              << std::endl;
    return false;
  }
  return true;
}

#define CHECK_ACL(call) CheckAcl((call), __FILE__, __LINE__)

// 默认 56 × 64 MiB ≈ 3.5 GiB 为真机带宽测量设计；仿真环境缩至 56 × 64 KB ≈ 3.5
// MB， 仍保证 DATA_BYTES / 4 能被 32/56/64 核整除。
constexpr int64_t DATA_BYTES = 56LL * 64 * 1024;
// clang-format off

constexpr int32_t DEFAULT_BUFFER_NUM_LIST[] = {
    2, 2, 2, 2, 2, 2, 2,
    3, 3, 3, 3, 3, 3,
    4, 4, 4, 4, 4, 4,
    6, 6, 6, 6, 6,
    8, 8, 8, 8, 8
};

constexpr int64_t DEFAULT_QUE_BYTES_LIST[] = {
    1024, 2 * 1024, 4 * 1024, 8 * 1024, 16 * 1024, 32 * 1024, 64 * 1024,
    1024, 2 * 1024, 4 * 1024, 8 * 1024, 16 * 1024, 32 * 1024,
    1024, 2 * 1024, 4 * 1024, 8 * 1024, 16 * 1024, 32 * 1024,
    1024, 2 * 1024, 4 * 1024, 8 * 1024, 16 * 1024,
    1024, 2 * 1024, 4 * 1024, 8 * 1024, 16 * 1024,
};
// clang-format on

#endif // BW_COMMON_H
