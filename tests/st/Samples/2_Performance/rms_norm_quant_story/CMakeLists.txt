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

configure_file(scripts/gen_data.py gen_data.py COPYONLY)

add_executable(rms_norm_quant_6_binary_sum src/6_binary_sum.asc)
target_compile_options(rms_norm_quant_6_binary_sum PRIVATE
    "$<$<COMPILE_LANGUAGE:ASC>:--npu-arch=${NPU_ARCH}>"
    "$<$<COMPILE_LANGUAGE:ASC>:-O3>"
)
target_link_libraries(rms_norm_quant_6_binary_sum PRIVATE
    m
    dl
    platform
    tiling_api
    ascendcl
    runtime
    stdc++
)
