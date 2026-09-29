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

"""Keep external CI's --cov=cannsim measuring the renamed source package."""

from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    source_root = Path(__file__).resolve().parents[2]
    legacy_root = source_root / "cannsim"
    current_root = source_root / "npusim"
    sources = getattr(config.option, "cov_source", None)
    if (
        not sources
        or True in sources
        or "cannsim" not in sources
        or getattr(config.option, "no_cov", False)
        or legacy_root.exists()
        or legacy_root.is_symlink()
        or not current_root.is_dir()
    ):
        return

    plugin = config.pluginmanager.getplugin("_cov")
    if plugin is None:
        return
    mapped = [str(current_root) if source == "cannsim" else source for source in sources]
    config.option.cov_source = mapped
    plugin.options.cov_source = mapped
    # pytest-cov starts before conftest loading. Restart its existing controller
    # before collection so the tests and imported npusim modules are measured.
    # Reusing the controller type retains pytest-cov's reporting/parallel options.
    controller = plugin.cov_controller
    if controller is not None:
        if controller.cov is not None:
            controller.cov.stop()
        plugin.start(type(controller))
    print("[pytest-ci] Compatibility: --cov=cannsim -> --cov=npusim")
