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

"""Configuration types for cannsim ktest (record -c mode)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass
class KtestConfig:
    """Input contract for the ktest mode triggered by ``record -c``."""

    case_dir: Union[str, Path]
    output: Path = Path(".")
    gen_report: str = ""
    model: str = "CA"
