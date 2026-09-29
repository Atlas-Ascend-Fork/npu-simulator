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

"""Runtime directory and environment helpers for cannsim record."""

from pathlib import Path
from typing import Optional

from npusim.core.public.comm import print_error, print_info
from npusim.core.public.soc_info import cores_to_mask
from npusim.core.record.paths import RecordPathError


# instr 类产物改由 archive_instr_bin_glob 按后缀 glob 匹配（*instr.bin），
# 统一覆盖 instr.bin / chip*_instr.bin 等多卡产物。
CAMODEL_ASSETS = (
    "log",
    "run_log",
    "stars_log*_task.dump",
)

RUNTIME_DIRS = ("log", "log/ub_log", "run_log")

_HELPER_LIB_NAME = "libcannsim_helper.so"
_REPORT_ARCH_BY_SOC = {
    "Ascend950": "v100",
    "Ascend950DT": "v100",
}


def prepare_runtime_dirs(app_cwd: Path, target_dir: Path) -> bool:
    app_cwd = Path(app_cwd).resolve()
    target_dir = Path(target_dir).resolve()

    for rel_dir in RUNTIME_DIRS:
        full_path = app_cwd / rel_dir
        try:
            full_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print_error(f"Failed to create directory {full_path}: {e}")
            return False

    log_ca_dir = target_dir / "record" / "log_ca"
    try:
        log_ca_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print_error(f"Failed to create log_ca directory {log_ca_dir}: {e}")
        return False

    folders = ", ".join(RUNTIME_DIRS)
    print_info(f"Runtime dirs ready: {folders} under {app_cwd}")
    print_info(f"log_ca under {target_dir}/record")
    return True


def _dedupe_existing_files(candidates):
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            yield resolved


def _find_cannsim_helper_lib(base_env: dict, ca_model_path: Path) -> Optional[Path]:
    """Find libcannsim_helper.so if it is installed or explicitly configured."""
    repo_root = Path(__file__).resolve().parents[3]
    ascend_toolkit = base_env.get("ASCEND_TOOLKIT_HOME", "")
    explicit = (
        base_env.get("CANNSIM_HELPER_LIB"),
        base_env.get("CANNSIM_HELPER_LIB_PATH"),
    )
    candidates = [
        *explicit,
        Path(ascend_toolkit) / "tools/simulator/bin" / _HELPER_LIB_NAME if ascend_toolkit else None,
        Path(ca_model_path) / _HELPER_LIB_NAME,
        repo_root.parent / "cannsim_helper" / "install/lib" / _HELPER_LIB_NAME,
    ]
    return next(_dedupe_existing_files(candidates), None)


def _prepend_ld_preload(existing: str, library: Path) -> str:
    parts = [str(library)]
    if existing:
        parts.append(existing)
    return " ".join(parts)


def build_execution_env(
    base_env: dict,
    ca_model_path: Path,
    record_dir: Path,
    origin_soc_version: str,
    resolved_cores: Optional[tuple],
    is_python_launch: bool,
    object_file: Optional[str] = None,
    model: str = "CA",
) -> dict:
    env = base_env.copy()
    existing_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{ca_model_path}:{existing_ld}" if existing_ld else str(ca_model_path)

    env["CAMODEL_SIM_TYPE"] = model

    if resolved_cores:
        mask = cores_to_mask(resolved_cores)
        env["CORE_ENABLE_MASK"] = f"0x{mask:x}"

    log_ca_dir = Path(record_dir) / "log_ca"
    env["CAMODEL_LOG_PATH"] = str(log_ca_dir)
    env["STARS_LOG_PATH"] = str(log_ca_dir)

    if origin_soc_version == "Ascend950DT":
        env["soc_version"] = "Ascend950DT"
    else:
        env.pop("soc_version", None)

    if is_python_launch:
        env["TASK_QUEUE_ENABLE"] = "0"

    helper_lib = _find_cannsim_helper_lib(env, ca_model_path)
    if helper_lib is None:
        # cannsim_helper 是唯一的采集通道，缺失时不能静默产出不完整数据。
        raise RecordPathError(
            f"{_HELPER_LIB_NAME} not found; cannot collect instruction data.\n"
            "  Searched: $CANNSIM_HELPER_LIB, $CANNSIM_HELPER_LIB_PATH,\n"
            "            $ASCEND_TOOLKIT_HOME/tools/simulator/bin/,\n"
            f"            {ca_model_path}/,\n"
            "            <repo>/../cannsim_helper/install/lib/\n"
            "  Install the library, or set CANNSIM_HELPER_LIB to its full path.",
            {"ca_model_path": str(ca_model_path)},
        )

    env["LD_PRELOAD"] = _prepend_ld_preload(
        env.get("LD_PRELOAD", ""),
        helper_lib,
    )
    env["CANNSIM_ARCH"] = origin_soc_version
    env["CANNSIM_REPORT_ARCH"] = _REPORT_ARCH_BY_SOC.get(
        origin_soc_version,
        "v100",
    )
    env["CANNSIM_HELPER_LAUNCH_DB_DIR"] = str(record_dir)
    env["CANNSIM_HELPER_LOG"] = str(Path(record_dir) / "cannsim_helper.log")
    if object_file:
        env["CANNSIM_OBJECT_PATH"] = str(Path(object_file).expanduser().resolve())
    else:
        env.pop("CANNSIM_OBJECT_PATH", None)
    print_info(f"cannsim_helper enabled: {helper_lib}")

    return env
