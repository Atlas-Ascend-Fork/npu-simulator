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

import runpy
from pathlib import Path

from setuptools import setup, find_packages

sub_packages = find_packages(where="npusim", exclude=["tests*"])
packages = ["npusim"] + ["npusim." + p for p in sub_packages]

# 版本单一真源是 npusim/version.py。wheel 分发名仍是 cannsim。
# 用 runpy 而不是 import，是为了在包尚未安装时也能取到。
_version_ns = runpy.run_path(str(Path(__file__).parent / "npusim" / "version.py"))

setup(
    name="cannsim",
    version=_version_ns["VERSION"],
    description="AscendOps performance simulation and analysis tool",
    python_requires=">=3.7",
    package_dir={"npusim": "npusim"},
    packages=packages,
    exclude_package_data={
        "npusim": ["setup.py", "build_wheel.sh"],
    },
    entry_points={
        "console_scripts": [
            "cannsim=npusim.main:main",
            "npusim=npusim.main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "npusim": [
            "exclude_instr.toml",
        ],
        "npusim.core.record": [
            "dlopen_scope_filter.c",
        ],
        "npusim.core.record.ktest": [
            "templates/*.json",
        ],
        "npusim.prof_demo": [
            "schema.sql",
        ],
        "npusim.prof": [
            "src/sql/*.sql",
            "static/arch_diagrams/**/*.svg",
        ],
    },
)
