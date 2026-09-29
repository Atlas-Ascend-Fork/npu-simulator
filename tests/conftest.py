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

"""Ensure source tree shadows any pre-installed npusim.

When pytest runs as a console script, cwd is NOT on sys.path. In CI, the base
image ships a stale cannsim==0.1.0 (or a CANN-bundled one) lacking newer
modules; without this shim the stale package wins, causing import errors and
spurious failures. Force the source root to sys.path[0] and purge any cached
cannsim modules so the source tree is always used.
"""

import os
import sys

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _root)

for _mod in list(sys.modules):
    if _mod == "npusim" or _mod.startswith("npusim."):
        del sys.modules[_mod]

# Load CI helpers from their non-package directory, including with pytest console scripts.
sys.path.insert(1, os.path.join(_root, ".gitcode", "scripts"))
pytest_plugins = ["pytest_ci_compat"]
