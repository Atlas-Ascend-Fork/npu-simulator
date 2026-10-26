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
"""Stage-3 helpers for ``npusim report`` (CSV export + .npu-rep packing).

These modules were previously standalone scripts under ``tools/`` shipped as
``npu-emulate`` console scripts. They are library-only now: the report flow
in :mod:`npusim.core.report.npu_rep_export` drives them in-process, and they
are not executable as standalone tools.
"""
