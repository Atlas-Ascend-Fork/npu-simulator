# -*- coding: utf-8 -*-
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

# 版本单一真源，供 setup.py 与 report 阶段的配对校验使用。
# cannsim_helper 是随 CANN 包提供的采集库 libcannsim_helper.so，
# 其源码由独立工程维护。修改这里时须同步 helper 工程的
# CMakeLists.txt 中 project(... VERSION ...)。
# report 阶段要求 KernelInfo.helper_version 与本版本完全一致。
MAJOR_VERSION = 0
MINOR_VERSION = 8
PATCH_VERSION = 0

VERSION = f"{MAJOR_VERSION}.{MINOR_VERSION}.{PATCH_VERSION}"
