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

import logging as log
from typing import List


class FinalLogger:
    def __init__(self) -> None:
        self.general_info_msg: List[str] = []
        self.kernel_info_msg: List[str] = []

    def add_general_info(self, message: str) -> None:
        self.general_info_msg.append(message)

    def add_kernel_info(self, message: str) -> None:
        self.kernel_info_msg.append(message)

    def report(self, return_code: int = 0) -> None:
        if return_code == 0:
            log.info("BiProf successfully finished kernel profiling, reports are ready for review")
        else:
            log.error("An error occurred during kernel profiling, please check logs")
        log.info("=" * 20)
        for message_group, group_name in [
            (self.general_info_msg, "Report INFO"),
            (self.kernel_info_msg, "Functions INFO"),
        ]:
            if len(message_group) != 0:
                log.info(group_name)
                for msg in message_group:
                    log.info(msg)
                log.info("=" * 20)


final_logger = FinalLogger()
