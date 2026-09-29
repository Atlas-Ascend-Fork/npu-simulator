# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the License);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an AS IS BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


cann_sample_check_arch(dav-3510)

add_executable(matmul main.asc)

target_compile_options(matmul PRIVATE
    "$<$<COMPILE_LANGUAGE:ASC>:--npu-arch=${NPU_ARCH}>"
    "$<$<COMPILE_LANGUAGE:ASC>:-O3>"
)

target_include_directories(matmul PRIVATE
    "${ASCEND_DIR}/asc"
)

if(NOT EXISTS "${ASCEND_DIR}/asc/include/tensor_api/tensor.h")
    message(FATAL_ERROR
        "CANN Tensor API is unavailable. The ST matmul sample requires a CANN "
        "package providing ${ASCEND_DIR}/asc/include/tensor_api/tensor.h."
    )
endif()

target_link_libraries(matmul PRIVATE
    m
    dl
    platform
    tiling_api
)
