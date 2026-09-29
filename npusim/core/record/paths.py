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

"""Path resolution for cannsim record."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RecordPaths:
    ascend_toolkit_path: Path
    ca_model_path: Path
    target_dir: Path
    record_dir: Path
    cannsim_log: Path


class RecordPathError(Exception):
    def __init__(self, message: str, context: dict = None):
        super().__init__(message)
        self.context = context or {}


_CAMODEL_DIR_BY_SOC = {
    "Ascend950": "Ascend950PR_9589",
    "Ascend950DT": "Ascend950PR_9599",
}


def _resolve_ca_model_path(ascend_toolkit: Path, soc_version: str) -> tuple[Path, str]:
    camodel_dir = _CAMODEL_DIR_BY_SOC.get(soc_version, soc_version)
    return ascend_toolkit / f"tools/simulator/{camodel_dir}/camodel", "ASCEND_TOOLKIT_HOME"


def resolve_record_paths(output: Path, soc_version: str, launch) -> tuple:
    ascend_toolkit_path = os.getenv("ASCEND_TOOLKIT_HOME", None)
    if not ascend_toolkit_path:
        raise RecordPathError(
            "Missing environment variable: ASCEND_TOOLKIT_HOME. Did you `source <ascend>/set_env.sh` first?"
        )

    ascend_toolkit = Path(ascend_toolkit_path)
    ca_model_path, ca_model_source = _resolve_ca_model_path(ascend_toolkit, soc_version)

    timestamp_str = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d%H%M%S")
    target_dir = Path(output).resolve() / f"npusim_{timestamp_str}_{launch.label}"
    record_dir = target_dir / "record"
    cannsim_log = target_dir / "npusim.log"

    context = {
        "USER_APP": launch.raw,
        "Launch kind": launch.kind,
        "Launch cwd": str(launch.cwd),
        "Archive label": launch.label,
        "Ascend Toolkit PATH": str(ascend_toolkit),
        "CAModel source": ca_model_source,
        "OUTPUT PATH": str(target_dir),
    }
    if launch.executable_path is not None:
        context["USER_APP PATH"] = str(launch.executable_path)

    if not ca_model_path.exists():
        raise RecordPathError(
            "CAModel path does not exist for the selected soc-version. Check ASCEND_TOOLKIT_HOME.",
            context,
        )

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        record_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise RecordPathError(
            f"Failed to create output directory: {e}\n"
            f"  Target: {target_dir}\n"
            f"  Hint: ensure write permission on parent directory.",
            context,
        )

    return (
        RecordPaths(
            ascend_toolkit_path=ascend_toolkit,
            ca_model_path=ca_model_path,
            target_dir=target_dir,
            record_dir=record_dir,
            cannsim_log=cannsim_log,
        ),
        context,
    )
