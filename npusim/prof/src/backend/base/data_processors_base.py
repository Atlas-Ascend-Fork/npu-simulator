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

import abc
from npusim.prof.src.common.logger import final_logger
from npusim.prof.src.common.biprof_context import biprof_ctx


class IBaseDataProcessor:
    def __init__(self) -> None:
        self._output_dir = biprof_ctx.output_dir
        self._analysis_name = biprof_ctx.analysis_type
        self._arch = biprof_ctx.arch

    @abc.abstractmethod
    def init(self) -> int:
        final_logger.add_general_info(f"Output folder: {self._output_dir}")
        return 0

    @abc.abstractmethod
    def process(self) -> int:  # Data processing AFTER the kernel's run
        pass


class IBaseReporter(IBaseDataProcessor):
    pass
