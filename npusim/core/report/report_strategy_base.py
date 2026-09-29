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

import importlib.util
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Backend availability — imported at module level so strategies can reference them.
# _BiProfRunner_module: lazily resolved by BiProfModuleStrategy.is_available()
#   (requires plotly); NOT imported at module level to avoid polluting import of
#   unrelated modules.
# --------------------------------------------------------------------------


def _check_plotly_available() -> bool:
    """主动检测当前 Python 环境中 plotly 是否可用。

    在导入 BiProfRunner（它会间接 import plotly）之前调用，
    避免在运行时才因 plotly 缺失而报错。多 Python 环境下会附带
    cannsim 实际使用的 Python 路径，方便用户定位。
    """
    if importlib.util.find_spec("plotly") is not None:
        return True
    _python_path = sys.executable or "python"
    print(
        f"HTML report require plotly\n"
        f"The executable PYTHON PATH: {_python_path}\n"
        f"Try install Plotly by: {_python_path} -m pip install --force-reinstall plotly\n",
        file=sys.stderr,
    )
    return False


def _try_import_biprof_runner():
    """旧 BiProfRunner 已删除。报告生成走 prof_demo，这里固定返回 None。"""
    return None


# BiProfRunner 模块的缓存：由 _try_import_biprof_runner() 延迟填充，
# 不再在模块导入时执行，避免影响无需 plotly 的调用路径。
_BiProfRunner_module: Optional[type] = None
_BiProfRunner_module_resolved: bool = False


def get_biprof_runner_module():
    """延迟获取 BiProfRunner 模块（带缓存）。

    首次调用时执行 plotly 检测 + 模块导入，后续直接返回缓存结果。
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
