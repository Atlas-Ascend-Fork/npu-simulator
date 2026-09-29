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

# 版本单一真源。setup.py 与 cannsim_helper 的配对校验都读这里。
# cannsim 与 cannsim_helper 强制同版本：report 阶段对 KernelInfo.helper_version
# 做精确匹配，不等即 fail-closed 退出。改这里时必须同步
# cannsim_helper/CMakeLists.txt 的 project(... VERSION ...)。
MAJOR_VERSION = 0
MINOR_VERSION = 8
PATCH_VERSION = 0

VERSION = f"{MAJOR_VERSION}.{MINOR_VERSION}.{PATCH_VERSION}"
