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

"""Runtime directory, case symlink and environment helpers for ktest mode."""

import fcntl
import json
import os
import shutil
import stat
from pathlib import Path
from typing import Optional

from npusim.core.public.comm import print_error, print_info, print_warn

from npusim.core.record.paths import RecordPathError
from npusim.core.record.runtime import (
    _find_cannsim_helper_lib as _find_helper_lib,
    _find_or_build_scope_filter,
    _prepend_ld_preload,
    _REPORT_ARCH_BY_SOC as _HELPER_REPORT_ARCH,
)


# 仿真模型当前在子进程 cwd（即 camodel_path）下写的运行时子目录。
# ktest 不通过 CAMODEL_LOG_PATH 重定向 log_ca，
# 因此 log_ca 也在 camodel_path 下创建，跑完后从 camodel_path 清理（不保留在 target_dir）。
KTEST_RUNTIME_DIRS = ("log", "log/ub_log", "run_log", "log_ca")

# 运行时拓扑模板：1p 表示不覆盖（不设 CAMODEL_CONFIG_PATH），
# 2p / 4p / 8p 对应随包安装的 JSON 模板。
_TEMPLATES_DIR = Path(__file__).parent / "templates"
RUNTIME_CONFIGS = {
    "1p": None,
    "2p": _TEMPLATES_DIR / "camodel_v100_2p.json",
    "4p": _TEMPLATES_DIR / "camodel_v100_8p.json",
    "8p": _TEMPLATES_DIR / "camodel_v100_8p.json",
}

# chip 数量 → runtime_config 名称映射
_CHIP_COUNT_TO_CONFIG = {
    1: "1p",
    2: "2p",
    4: "4p",
    8: "8p",
}


def detect_runtime_config(case_dir: Path) -> str:
    """读取 ``case_dir/top.json``，根据 ``chip*`` 键数量推断运行时拓扑。

    top.json 结构示例::

        {
          "chip0": {"die0": {...}},
          "chip1": {"die0": {...}}
        }

    两个 chip → "2p"；四个 chip → "4p"；八个 chip → "8p"；
    单 chip → "1p"（不覆盖模板）。

    Args:
        case_dir: case 目录路径，其下必须存在 ``top.json``。

    Returns:
        runtime_config 字符串，如 ``"2p"``。

    Raises:
        ValueError: top.json 缺失 / 解析失败 / chip 数量非法。
    """
    case_dir = Path(case_dir).resolve()
    top_json = case_dir / "top.json"
    if not top_json.exists():
        raise ValueError(
            f"top.json not found under case directory: {case_dir}. "
            f"ktest mode requires a top.json describing chip topology."
        )

    try:
        with open(top_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"Failed to parse {top_json}: {e}. Ensure it is valid JSON with chipN keys.") from e

    if not isinstance(data, dict):
        raise ValueError(f"Invalid top.json: expected a JSON object at top level, got {type(data).__name__}.")

    chip_count = sum(1 for k in data.keys() if str(k).startswith("chip"))
    if chip_count == 0:
        raise ValueError(f"No 'chip*' keys found in {top_json}. Expected keys like chip0, chip1, ...")

    config = _CHIP_COUNT_TO_CONFIG.get(chip_count)
    if config is None:
        raise ValueError(
            f"Unsupported chip count {chip_count} in {top_json}. Supported: {sorted(_CHIP_COUNT_TO_CONFIG.keys())}."
        )
    return config


def resolve_config_template(runtime_config: str) -> Optional[Path]:
    """根据 runtime_config 取值返回对应的模板文件路径。

    ``1p`` 返回 None（不设 CAMODEL_CONFIG_PATH）；
    ``2p`` / ``4p`` / ``8p`` 返回随包模板路径。
    非法取值抛 ValueError，由调用方在 stage1 捕获。
    """
    template = RUNTIME_CONFIGS.get(runtime_config)
    if template is None:
        return None
    if not template.exists():
        raise ValueError(f"Runtime config template missing: {template}. Check that cannsim is installed completely.")
    return template


def prepare_runtime_dirs(camodel_path: Path) -> bool:
    """在 camodel_path 下创建仿真模型所需的运行时子目录。

    模型当前不会自己 mkdir，目录必须预先存在。
    """
    camodel_path = Path(camodel_path).resolve()

    for rel_dir in KTEST_RUNTIME_DIRS:
        full_path = camodel_path / rel_dir
        try:
            full_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print_error(f"Failed to create directory {full_path}: {e}")
            return False

    folders = ", ".join(KTEST_RUNTIME_DIRS)
    print_info(f"Runtime dirs ready: {folders} under {camodel_path}")
    return True


def ensure_sim_executable(sim_path: Path) -> bool:
    """在保留原有权限位的基础上为 sim 补齐执行位。

    安装包里的 sim 可能不带执行权限，每次执行前统一补上，避免依赖用户手动 chmod。
    """
    try:
        st = os.stat(str(sim_path))
        os.chmod(str(sim_path), st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as e:
        print_error(f"Failed to set executable permission on {sim_path}: {e}")
        return False
    print_info(f"Ensured executable permission on {sim_path}")
    return True


def create_case_symlink(case_symlink: Path, case_dir: Path) -> bool:
    """在 camodel_path 下创建名为 ``case`` 的软链接指向 ``case_dir``。

    若已存在同名路径（软链/文件/目录），先清理再重建，保证幂等。
    """
    case_dir = Path(case_dir).resolve()
    case_symlink = Path(case_symlink)

    if not case_dir.exists():
        print_error(f"case directory does not exist: {case_dir}")
        return False
    if not case_dir.is_dir():
        print_error(f"case path is not a directory: {case_dir}")
        return False

    if case_symlink.is_symlink() or case_symlink.exists():
        try:
            if case_symlink.is_symlink() or case_symlink.is_file():
                case_symlink.unlink()
            else:
                shutil.rmtree(str(case_symlink))
        except Exception as e:
            print_error(f"Failed to remove existing {case_symlink}: {e}")
            return False

    try:
        os.symlink(str(case_dir), str(case_symlink))
    except Exception as e:
        print_error(f"Failed to create symlink {case_symlink} -> {case_dir}: {e}")
        return False

    print_info(f"Symlink created: {case_symlink} -> {case_dir}")
    return True


def remove_case_symlink(case_symlink: Path) -> None:
    """清理 camodel_path 下的 ``case`` 软链接。

    仅当目标确实是软链接时才 unlink，避免误删真实目录/文件。
    失败只打 warn，不抛异常，避免影响主流程的退出路径。
    """
    if case_symlink is None:
        return
    case_symlink = Path(case_symlink)
    if not case_symlink.is_symlink():
        return
    try:
        case_symlink.unlink()
    except Exception as e:
        print_warn(f"Failed to remove symlink {case_symlink}: {e}")


_KTEST_SOC_VERSION = "Ascend950"
_HELPER_LIB_NAME = "libcannsim_helper.so"


def build_execution_env(
    base_env: dict,
    camodel_path: Path,
    record_dir: Path,
    config_template: Optional[Path] = None,
    model: str = "CA",
) -> dict:
    """构建 sim 子进程环境变量。

    - 固定注入 ``CAMODEL_CALL_TYPE=1``（ktest 的核心 env）。
    - 注入 ``LD_LIBRARY_PATH``，让 sim 找到 camodel 目录下的 ``.so``。
    - 当 ``config_template`` 非 None 时，注入 ``CAMODEL_CONFIG_PATH`` 指向模板 json，
      由 sim 读取，不再替换 camodel_path 下的 camodel_v100.json。
    - 注入 ``CAMODEL_SIM_TYPE`` 指定仿真模型类型（CA / PV）。
    - 注入 cannsim_helper：ktest 的 ``./sim`` 不经过 ``rtKernelLaunch``，
      launch-split 不会开库，因此必须设 ``CANNSIM_HELPER_PER_CHIP_DB=1``
      让 helper 按 per-chip 路由惰性建 ``chip{N}.db``。
    - 不设 ``CAMODEL_LOG_PATH`` / ``STARS_LOG_PATH``：log_ca 落在 camodel_path 下，
      跑完后随其它产物一起搬到 target_dir。
    - 不设 ``CORE_ENABLE_MASK`` / ``TASK_QUEUE_ENABLE``。

    Args:
        base_env: 基础环境变量（通常为 ``os.environ`` 的 copy）。
        camodel_path: camodel 目录（sim 的 cwd）。
        record_dir: 归档目录，helper DB 和日志写到这里。
        config_template: 可选的 camodel 配置模板路径。
        model: 仿真模型类型，``CA``（精确计时）或 ``PV``（功能验证）。

    Raises:
        RecordPathError: 找不到 ``libcannsim_helper.so``。
    """
    env = base_env.copy()
    existing_ld = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{camodel_path}:{existing_ld}" if existing_ld else str(camodel_path)

    env["CAMODEL_CALL_TYPE"] = "1"
    env["CAMODEL_SIM_TYPE"] = model

    if config_template is not None:
        env["CAMODEL_CONFIG_PATH"] = str(config_template)

    # --- cannsim_helper injection (same discovery as record mode) ---
    helper_lib = _find_helper_lib(env, camodel_path)
    if helper_lib is None:
        raise RecordPathError(
            f"{_HELPER_LIB_NAME} not found; cannot collect instruction data.\n"
            "  Searched: $CANNSIM_HELPER_LIB, $CANNSIM_HELPER_LIB_PATH,\n"
            "            $ASCEND_TOOLKIT_HOME/tools/simulator/bin/,\n"
            f"            {camodel_path}/,\n"
            "            <repo>/../cannsim_helper/install/lib/\n"
            "  Install the library, or set CANNSIM_HELPER_LIB to its full path.",
            {"camodel_path": str(camodel_path)},
        )

    scope_filter = _find_or_build_scope_filter()
    if scope_filter is not None:
        env["LD_PRELOAD"] = _prepend_ld_preload(env.get("LD_PRELOAD", ""), scope_filter)
        print_info(f"dlopen scope filter enabled (ktest): {scope_filter}")

    env["LD_PRELOAD"] = _prepend_ld_preload(
        env.get("LD_PRELOAD", ""),
        helper_lib,
    )
    env["CANNSIM_ARCH"] = _KTEST_SOC_VERSION
    env["CANNSIM_REPORT_ARCH"] = _HELPER_REPORT_ARCH.get(
        _KTEST_SOC_VERSION,
        "v100",
    )
    env["CANNSIM_HELPER_LAUNCH_DB_DIR"] = str(record_dir)
    env["CANNSIM_HELPER_LOG"] = str(Path(record_dir) / "cannsim_helper.log")
    # ktest ./sim has no launch intercept; per-chip DB is the only way to get DBs.
    env["CANNSIM_HELPER_PER_CHIP_DB"] = "1"
    print_info(f"cannsim_helper enabled (ktest): {helper_lib}")

    return env


# camodel 目录下的排他锁文件名。
# record -c 模式通过 case 软链、log/、log_ca/ 等共享路径使用 camodel 目录，
# 同一 CANN 环境下并发运行会互相踩踏，因此用 flock 互斥。
_LOCK_FILE_NAME = ".npusim_ktest.lock"


def acquire_camodel_lock(camodel_path: Path) -> Optional[object]:
    """尝试对 camodel 目录加排他锁。

    使用 ``fcntl.flock`` 非阻塞模式获取；进程退出/崩溃时内核自动释放锁。

    Returns:
        成功返回锁文件句柄（调用方需持有直到流程结束）；失败返回 None。
    """
    lock_path = Path(camodel_path) / _LOCK_FILE_NAME
    try:
        lock_fp = open(lock_path, "a+")
    except Exception as e:
        print_error(f"Failed to open lock file {lock_path}: {e}")
        return None

    try:
        fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        lock_fp.seek(0)
        pid_str = lock_fp.read().strip() or "unknown"
        lock_fp.close()
        print_error(
            f"Another npusim record -c process (PID {pid_str}) is using "
            f"the camodel environment. "
            f"Please wait for the other process to finish, "
            f"or kill it if stale (kill {pid_str})."
        )
        return None

    lock_fp.seek(0)
    lock_fp.truncate()
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()
    return lock_fp


def release_camodel_lock(lock_fp) -> None:
    """释放 camodel 锁。

    关闭文件句柄即可释放内核 flock；失败只打 warn，不影响主退出路径。
    """
    if lock_fp is None:
        return
    try:
        lock_fp.close()
    except Exception as e:
        print_warn(f"Failed to release camodel lock: {e}")
