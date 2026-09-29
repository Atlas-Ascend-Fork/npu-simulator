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

"""SoC metadata and core-id / CORE_ENABLE_MASK helpers."""

from typing import Dict, Iterable, Optional, Tuple

# 单个 SoC 版本的规格定义，键：max_cores (int)。
# typing.TypedDict 是 3.8+，而 setup.py 声明 python_requires=">=3.7"，
# 且 typing_extensions 不是本项目依赖，故用普通 Dict 表达。
SocSpec = Dict[str, int]


SOC_INFO: Dict[str, SocSpec] = {
    "Ascend950": {
        "max_cores": 32,
    },
    "Ascend950DT": {
        "max_cores": 32,
    },
}

# record 默认启用核 0；与 `-g` 触发的 auto-report 缺省一致。
DEFAULT_CORE_ID: Tuple[int, ...] = (0,)

# instr.bin 解码时的 core_id 归一化基数（取所有已知 SoC 的最大值）。
# 消费方在 cannsim_helper/tools/compare_instr_bin_db_auto.py。
_DEFAULT_MAX_CORES: int = max(s["max_cores"] for s in SOC_INFO.values())


def get_ai_core_count(soc_version: str) -> int:
    """返回指定 SoC 的 AI core 数量（即 CORE_ENABLE_MASK 的有效位宽）。

    Raises:
        ValueError: 当 ``soc_version`` 未在 SOC_INFO 中注册时。
    """
    info = SOC_INFO.get(soc_version)
    if info is None:
        supported = list(SOC_INFO.keys())
        raise ValueError(f"Unknown soc-version '{soc_version}'. Supported: {supported}")
    return info["max_cores"]


def parse_core_id(
    core_id: Optional[str],
    max_cores: int,
    default: Tuple[int, ...] = DEFAULT_CORE_ID,
) -> Tuple[int, ...]:
    """解析 core-id 字符串为整数核心索引元组，同时做越界校验。

    支持格式与 ``npusim report -n`` 完全一致：

    - ``None`` / ``""``   → ``default``
    - ``"all"``           → ``(0, 1, ..., max_cores - 1)``
    - ``"5"``             → ``(5,)``
    - ``"0-2,12-14"``     → ``(0, 1, 2, 12, 13, 14)``

    Args:
        core_id:   CLI 传入的字符串，或 None/空串。
        max_cores: 该 SoC 的 AI core 数量（由 :func:`get_ai_core_count` 给出）。
        default:   空输入时的兜底返回值。

    Raises:
        ValueError: 语法错误或越界时。
    """
    if not core_id:
        return default

    if max_cores <= 0:
        raise ValueError(f"max_cores must be positive, got {max_cores}")

    normalized = core_id.strip().lower()
    if normalized == "all":
        return tuple(range(max_cores))

    result = []
    for part in normalized.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start_str, end_str = part.split("-", 1)
                lo, hi = int(start_str), int(end_str)
            else:
                lo = hi = int(part)
        except ValueError as exc:
            raise ValueError(f"Invalid core-id token '{part}'; expected integer or 'a-b' range.") from exc

        if lo < 0 or hi >= max_cores or lo > hi:
            raise ValueError(f"Core range '{part}' out of bounds [0, {max_cores - 1}]")
        result.extend(range(lo, hi + 1))

    if not result:
        return default
    return tuple(sorted(set(result)))


def cores_to_mask(cores: Iterable[int]) -> int:
    """把核心索引序列转成 ``CORE_ENABLE_MASK`` 位图（bit_i=1 表示打开核 i 的日志）。"""
    mask = 0
    for c in cores:
        mask |= 1 << int(c)
    return mask
