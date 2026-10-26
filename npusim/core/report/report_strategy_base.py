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

"""
Shared backend detection and the abstract strategy base class for report generation.

This module is intentionally split from the concrete strategy implementations to keep
each piece focused and easier to navigate.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Backend names — single source of truth for post-processing decisions keyed
# on the backend name (report reorganization, npu-rep export).
# --------------------------------------------------------------------------

BIPROF_BACKEND_NAME = "BiProfRunner module"
PROF_DEMO_BACKEND_NAME = "prof_demo"

# Backends whose output layout needs the core-based report reorganization.
REORGANIZE_BACKENDS = frozenset({BIPROF_BACKEND_NAME, PROF_DEMO_BACKEND_NAME})

# --------------------------------------------------------------------------
# Backend availability — imported at module level so strategies can reference them.
# _BiProfRunner_module: lazily resolved by BiProfModuleStrategy.is_available()
#   (stdlib-only engine); NOT imported at module level to avoid polluting import of
#   unrelated modules.
# --------------------------------------------------------------------------


def _try_import_biprof_runner():
    """尝试导入 BiProfRunner 模块。

    专供 BiProfModuleStrategy.is_available() 调用，不污染模块级导入。

    Returns:
        模块对象，或 None（ImportError 时静默跳过，触发 fallback）。
    """
    try:
        from npusim.prof.src.runner import BiProfRunner as _mod  # type: ignore

        return _mod
    except ImportError:
        return None


# BiProfRunner 模块的缓存：由 _try_import_biprof_runner() 延迟填充，
# 不在模块导入时执行，避免影响无需引擎的调用路径。
_BiProfRunner_module: Optional[type] = None
_BiProfRunner_module_resolved: bool = False


def get_biprof_runner_module():
    """延迟获取 BiProfRunner 模块（带缓存）。

    首次调用时执行模块导入，后续直接返回缓存结果。
    """
    global _BiProfRunner_module, _BiProfRunner_module_resolved
    if not _BiProfRunner_module_resolved:
        _BiProfRunner_module = _try_import_biprof_runner()
        _BiProfRunner_module_resolved = True
    return _BiProfRunner_module


# soc_version -> BiProfRunner -a 参数映射
SOC_TO_ARCH: Dict[str, str] = {
    "Ascend950": "v100",
}


# =============================================================================
# Abstract Strategy Base
# =============================================================================


class ReportStrategy(ABC):
    """Abstract base for report backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for logging."""

    @abstractmethod
    def is_available(self) -> bool:
        """Static check: can this backend be used at all?"""

    @abstractmethod
    def execute(
        self,
        biprof_args: List[str],
        export_abs: Path,
        output_abs: Path,
        core_ids: tuple,
    ) -> Tuple[str, bool]:
        """
        Run the report generator.

        Args:
            biprof_args: Arguments for BiProfRunner
            export_abs: Export directory containing cannsim_helper DB files
            output_abs: Output directory for reports
            core_ids: Core IDs to process

        Returns:
            (backend_name, success): backend_name is strategy.name, success is True
            if the backend succeeded, False if it failed and fallback should continue.
        """
