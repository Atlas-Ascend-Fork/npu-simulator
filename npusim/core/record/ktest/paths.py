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

"""Path resolution for cannsim ktest (record -c mode)."""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class KtestPaths:
    ascend_home: Path
    camodel_path: Path
    sim_path: Path
    case_symlink: Path
    target_dir: Path
    record_dir: Path


class KtestPathError(Exception):
    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.context = context or {}


def resolve_ktest_paths(output: Path, label: str = "case") -> tuple:
    """解析 ASCEND_HOME_PATH / camodel / sim 路径并创建归档目录。

    与 record 的 ``resolve_record_paths`` 对齐，但环境变量来源是
    ``ASCEND_HOME_PATH``（ktest 专属），模型目录硬编码为 ``dav_3510``。
    归档目录统一使用 ``npusim_`` 前缀，与 record 路径保持一致。
    """
    ascend_home_str = os.getenv("ASCEND_HOME_PATH", None)
    if not ascend_home_str:
        raise KtestPathError(
            "Missing environment variable: ASCEND_HOME_PATH. Did you `source <ascend>/set_env.sh` first?"
        )

    ascend_home = Path(ascend_home_str)
    camodel_path = ascend_home / "tools/simulator/dav_3510/camodel"
    sim_path = camodel_path / "sim"
    case_symlink = camodel_path / "case"

    timestamp_str = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d%H%M%S")
    target_dir = Path(output).resolve() / f"npusim_{timestamp_str}_{label}"
    record_dir = target_dir / "record"

    context = {
        "ASCEND_HOME_PATH": str(ascend_home),
        "CAModel PATH": str(camodel_path),
        "sim PATH": str(sim_path),
        "case symlink": str(case_symlink),
        "OUTPUT PATH": str(target_dir),
    }

    if not camodel_path.exists():
        raise KtestPathError(
            f"CAModel path does not exist: {camodel_path}. "
            f"Check that ASCEND_HOME_PATH provides tools/simulator/dav_3510/camodel.",
            context,
        )

    if not sim_path.exists():
        raise KtestPathError(
            f"sim executable does not exist: {sim_path}. Check that toolkit provides simulator/dav_3510/camodel/sim.",
            context,
        )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        record_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise KtestPathError(
            f"Failed to create output directory: {e}\n"
            f"  Target: {target_dir}\n"
            f"  Hint: ensure write permission on parent directory.",
            context,
        )

    return (
        KtestPaths(
            ascend_home=ascend_home,
            camodel_path=camodel_path,
            sim_path=sim_path,
            case_symlink=case_symlink,
            target_dir=target_dir,
            record_dir=record_dir,
        ),
        context,
    )
