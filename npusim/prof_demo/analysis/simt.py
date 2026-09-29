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
"""Static VF_SIMT IPC for summary and call events.

SIMT leaf events are not displayed by the legacy ordinary trace. Their dynamic
IPC and assembly-export tables therefore have no consumers in this module.
"""

from npusim.prof_demo.database import database_handler
from npusim.prof_demo.analysis.vf import VfCallAnalyzerBase, VF_CLASS_EXECUTION, VF_CLASS_LOAD_STORE, VF_CLASS_BRANCH


class Ascend950VFSimtIPCAnalyzer(VfCallAnalyzerBase):
    VF_NAMES = ("VF_SIMT",)
    CLASS_COLUMN = "ExecInstrVfSimtClass"
    CLASS_IDS = (VF_CLASS_EXECUTION, VF_CLASS_LOAD_STORE, VF_CLASS_BRANCH)
    EXCLUDED_INSTRS = ("SIMT_END",)

    def process(self):
        for vf_instr_id, instrs_of_vf in self.iter_vfs():
            if not instrs_of_vf:
                continue
            database_handler.execute(
                "INSERT INTO VfSimtIPC VALUES (?, ?, ?, ?)", (vf_instr_id, *self.static_ipc(instrs_of_vf))
            )
