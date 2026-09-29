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


cmake_minimum_required(VERSION 3.16)

set(VALID_NPU_ARCHS dav-3510 dav-2201)
if(NOT DEFINED NPU_ARCH OR NPU_ARCH STREQUAL "")
    message(FATAL_ERROR "NPU_ARCH is required. Example: cmake -S . -B build -DNPU_ARCH=dav-3510")
endif()
if(NOT "${NPU_ARCH}" IN_LIST VALID_NPU_ARCHS)
    message(FATAL_ERROR "Unsupported NPU_ARCH: ${NPU_ARCH}. Supported values: dav-3510, dav-2201")
endif()

# Locate the Ascend toolkit and configure the Bisheng compilers before
# project() initializes the C/C++/ASC languages.
set(_st_ascend_processor "${CMAKE_SYSTEM_PROCESSOR}")
if(NOT _st_ascend_processor)
    set(_st_ascend_processor "${CMAKE_HOST_SYSTEM_PROCESSOR}")
endif()
if(NOT _st_ascend_processor)
    execute_process(
        COMMAND uname -m
        OUTPUT_VARIABLE _st_ascend_processor
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
endif()
set(SYSTEM_PREFIX "${_st_ascend_processor}-linux")
unset(_st_ascend_processor)

if(DEFINED ENV{ASCEND_HOME_PATH})
    set(ASCEND_DIR $ENV{ASCEND_HOME_PATH})
else()
    message(WARNING "Environment variable ASCEND_HOME_PATH is not set. Using default path.")
    if("$ENV{USER}" STREQUAL "root")
        message(STATUS "Running as root user, checking default root paths for Ascend toolkit.")
        if(EXISTS /usr/local/Ascend/ascend-toolkit/latest)
            set(ASCEND_DIR /usr/local/Ascend/ascend-toolkit/latest)
        elseif(EXISTS /usr/local/Ascend/latest)
            set(ASCEND_DIR /usr/local/Ascend/latest)
        else()
            message(FATAL_ERROR "Ascend toolkit not found in default root paths. Please set ASCEND_HOME_PATH.")
        endif()
    else()
        message(STATUS "Running as non-root user, checking default user paths for Ascend toolkit.")
        if(EXISTS $ENV{HOME}/Ascend/ascend-toolkit/latest)
            set(ASCEND_DIR $ENV{HOME}/Ascend/ascend-toolkit/latest)
        elseif(EXISTS $ENV{HOME}/Ascend/latest)
            set(ASCEND_DIR $ENV{HOME}/Ascend/latest)
        else()
            message(FATAL_ERROR "Ascend toolkit not found in default user paths. Please set ASCEND_HOME_PATH.")
        endif()
    endif()
endif()

message(STATUS "Using Ascend toolkit path: ${ASCEND_DIR}")
set(CMAKE_PREFIX_PATH ${ASCEND_DIR}/)
set(BISHENG "${ASCEND_DIR}/${SYSTEM_PREFIX}/ccec_compiler/bin/bisheng" CACHE FILEPATH "Path to Bisheng compiler")
message(STATUS "Bisheng compiler path: ${BISHENG}")

if(NOT DEFINED CMAKE_C_COMPILER)
    set(CMAKE_C_COMPILER "${BISHENG}" CACHE FILEPATH "C compiler" FORCE)
endif()

if(NOT DEFINED CMAKE_CXX_COMPILER)
    set(CMAKE_CXX_COMPILER "${BISHENG}" CACHE FILEPATH "CXX compiler" FORCE)
endif()

if(NOT DEFINED CMAKE_LINKER)
    set(CMAKE_LINKER "${BISHENG}" CACHE FILEPATH "Linker" FORCE)
endif()

find_package(ASC)
project(npu_simulator_st_samples LANGUAGES C CXX ASC)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

macro(cann_sample_check_arch)
    set(_cann_sample_expected_archs ${ARGN})
    file(RELATIVE_PATH _cann_sample_path ${PROJECT_SOURCE_DIR} ${CMAKE_CURRENT_SOURCE_DIR})
    if(NOT "${NPU_ARCH}" IN_LIST _cann_sample_expected_archs)
        message(STATUS "Skip sample ${_cann_sample_path}: NPU_ARCH=${NPU_ARCH} is not supported")
        return()
    endif()
    unset(_cann_sample_path)
    unset(_cann_sample_expected_archs)
endmacro()

add_subdirectory(Samples/0_Introduction/matmul)
add_subdirectory(Samples/0_Introduction/vector_add)
add_subdirectory(Samples/1_Features/hardware_features/cv_datapath)
add_subdirectory(Samples/1_Features/hardware_features/hif8)
add_subdirectory(Samples/1_Features/hardware_features/mem_bandwidth)
add_subdirectory(Samples/2_Performance/rms_norm_quant_story)
add_subdirectory(Samples/3_Utilities/simulation-based-vf-profiling)

# The runner copies each target's binary and runtime Python scripts together,
# so an in-tree build can be used directly without a separate install step.
set(ST_SAMPLES_BINARY_DIR "${CMAKE_BINARY_DIR}/Samples" CACHE PATH "Built ST samples root")
