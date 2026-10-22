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

import os
import shutil
import json
import subprocess

from npusim.core.public.comm import print_error, print_info, print_warn
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


_SCOPE_FILTER_SRC = "dlopen_scope_filter.c"
_SCOPE_FILTER_LIB = "libdlopen_scope_filter.so"


def _find_or_build_scope_filter() -> Optional[Path]:
    """查找（或现场编译）dlopen 作用域过滤器，供 LD_PRELOAD 前置使用。

    CANN 的 libascendcl.so 以 RTLD_GLOBAL 方式 dlopen libacl_rt.so；仿真环境下
    LD_LIBRARY_PATH 前置 camodel 目录，使 libacl_rt 的依赖闭包解析到模拟器 stub 链
    （libTgWrapper/libge_compiler，导出旧 ABI 的 libstdc++ weak 符号）。整链被提升进
    全局作用域后，同进程第三方新 ABI 代码的 std::regex 等符号会被旧副本插入，触发
    std::bad_alloc。过滤器剥掉这些库的 RTLD_GLOBAL 位，让闭包留在私有作用域
    （与上板进程行为一致）。NPUSIM_SCOPE_FILTER=0 可关闭。
    """
    if os.environ.get("NPUSIM_SCOPE_FILTER", "1") == "0":
        return None
    module_dir = Path(__file__).resolve().parent
    candidates = [
        Path(os.environ["NPUSIM_SCOPE_FILTER_LIB"]) if os.environ.get("NPUSIM_SCOPE_FILTER_LIB") else None,
        module_dir / _SCOPE_FILTER_LIB,
    ]
    found = next(_dedupe_existing_files(candidates), None)
    if found is not None:
        return found
    src = module_dir / _SCOPE_FILTER_SRC
    if not src.is_file():
        print_warn(f"dlopen scope filter source not found: {src}")
        return None
    cache_lib = Path.home() / ".cache" / "npusim" / _SCOPE_FILTER_LIB
    try:
        cache_lib.parent.mkdir(parents=True, exist_ok=True)
        if not cache_lib.is_file() or cache_lib.stat().st_mtime < src.stat().st_mtime:
            cc = shutil.which("cc") or shutil.which("gcc")
            if cc is None:
                print_warn("no cc/gcc available, skip dlopen scope filter")
                return None
            subprocess.run(
                [cc, "-shared", "-fPIC", "-O2", "-o", str(cache_lib), str(src)],
                check=True,
                capture_output=True,
            )
    except Exception as e:
        print_warn(f"failed to build dlopen scope filter: {e}")
        return None
    return cache_lib


# Collective-communication (CC) simulation mock stack (opts-in automatically when chip count > 1).
# The mock libhccl_fwk_camodel.so ships inside the CANN package (not the wheel):
# $ASCEND_TOOLKIT_HOME/tools/simulator/<arch>/camodel/libhccl_fwk_camodel.so.
_CC_ARCH_DIR_BY_SOC = {
    "Ascend950": "dav_3510",
    "Ascend950DT": "dav_3510",
}

# Mirror of HCCL_MTE_MAX_RANK_NUM (HcclCombinOpParam950 windowsIn capacity)
# in the CANN-packaged mock libhccl_fwk_camodel.so.
CC_MAX_CHIP = 64


def _cc_mock_so(env: dict, soc_version: str):
    """Path of the CANN-packaged mock libhccl_fwk_camodel.so; None if unresolvable."""
    toolkit = env.get("ASCEND_TOOLKIT_HOME") or env.get("ASCEND_HOME_PATH") or ""
    arch_dir = _CC_ARCH_DIR_BY_SOC.get(soc_version)
    if not toolkit or not arch_dir:
        return None
    return Path(toolkit) / "tools" / "simulator" / arch_dir / "camodel" / "libhccl_fwk_camodel.so"


def cc_chip_of(environ: dict) -> int:
    """Effective CC chip count (number of simulated cards)."""
    raw = environ.get("CAMODEL_SIM_RANK_SIZE", "1") or "1"
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _template_chip_count(tpl: Path):
    """Parse number_of_chip from a camodel config template; None if not parseable."""
    try:
        with open(tpl, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return int(data["sysconf"]["number_of_chip"])
    except Exception:
        return None


def apply_cc_env(env: dict, cc_chip: int, soc_version: str) -> None:
    """Inject the CC sim environment when a multi-chip simulation is requested."""
    if cc_chip <= 1:
        return
    toolkit = env.get("ASCEND_TOOLKIT_HOME") or env.get("ASCEND_HOME_PATH") or ""
    env.setdefault("CAMODEL_SIM_RANK_SIZE", str(cc_chip))
    env.setdefault("CAMODEL_SIM_BUFFSIZE_MB", "200")
    env["CANNSIM_HELPER_PER_CHIP_DB"] = "1"
    env["CANNSIM_HELPER_SYNC_AFTER_LAUNCH"] = "0"
    if not env.get("ASCEND_OPP_PATH") and toolkit:
        env["ASCEND_OPP_PATH"] = os.path.join(toolkit, "opp")
    # camodel topology (number_of_chip) must be >= chip count; auto-select a template.
    if not env.get("CAMODEL_CONFIG_PATH") and toolkit:
        tpl_dir = Path(toolkit) / "python/site-packages/cannsim/core/record/ktest/templates"
        exact = tpl_dir / f"camodel_v100_{cc_chip}p.json"
        if exact.exists():
            env["CAMODEL_CONFIG_PATH"] = str(exact)
        else:
            best = None  # (chips, path) minimal chips >= chip count
            largest = None
            for cand in sorted(tpl_dir.glob("camodel_v100_*p.json")):
                chips = _template_chip_count(cand)
                if chips is None:
                    continue
                if largest is None or chips > largest[0]:
                    largest = (chips, cand)
                if chips >= cc_chip and (best is None or chips < best[0]):
                    best = (chips, cand)
            if best is not None:
                env["CAMODEL_CONFIG_PATH"] = str(best[1])
            elif largest is not None:
                raise RecordPathError(
                    f"no camodel template supports chip count {cc_chip}: largest available is "
                    f"{largest[0]}-chip ({largest[1].name}); reduce --chip / "
                    "CAMODEL_SIM_RANK_SIZE or set CAMODEL_CONFIG_PATH",
                    {"template_dir": str(tpl_dir)},
                )
    # ASCEND_HOME_PATH must point at a root whose x86_64-linux/lib64/libhccl_fwk.so
    # and libhccl.so are swapped to the mock; prefer external root, else real CANN.
    env.setdefault("ASCEND_HOME_PATH", toolkit)


def cc_swap_hccl(env: dict, soc_version: str):
    """Point CANN libhccl_fwk.so / libhccl.so at the camodel mock (return restore handle)."""
    mock_so = _cc_mock_so(env, soc_version)
    if mock_so is None:
        print_warn(
            f"cannot locate the CC mock for soc {soc_version}: unknown soc version or "
            "missing ASCEND_TOOLKIT_HOME/ASCEND_HOME_PATH; HCCL mock not injected."
        )
        return None
    if not mock_so.exists():
        print_warn(
            f"CC mock {mock_so} not found; HCCL mock not injected. Put the mock "
            "libhccl_fwk_camodel.so there to enable CC simulation."
        )
        return None
    toolkit = env.get("ASCEND_TOOLKIT_HOME") or env.get("ASCEND_HOME_PATH") or ""
    lib_dir = Path(toolkit) / "x86_64-linux" / "lib64"
    fwk_target = lib_dir / "libhccl_fwk.so"
    if not fwk_target.exists():
        return None
    handles = []
    for target in (fwk_target, lib_dir / "libhccl.so"):
        if not target.exists():
            # e.g. cann-9.2.0 ships no libhccl.so: create a fresh symlink
            # and remove it again on restore.
            target.symlink_to(str(mock_so))
            handles.append({"target": target, "created": True})
            continue
        orig = None
        if target.is_symlink():
            orig = os.readlink(target)
            target.unlink()
        else:
            shutil.copy2(str(target), str(target) + ".ccbak")
            target.unlink()
        target.symlink_to(str(mock_so))
        handles.append({"target": target, "orig": orig})
    return handles


def cc_restore_hccl(handles) -> None:
    """Restore the real-CANN HCCL entries after the sim."""
    if not handles:
        return
    for handle in handles:
        target = handle["target"]
        if handle.get("created"):
            if target.is_symlink():
                target.unlink()
            continue
        orig = handle["orig"]
        if not target.is_symlink():
            continue
        target.unlink()
        if orig is None:
            bak = str(target) + ".ccbak"
            if Path(bak).exists():
                shutil.copy2(bak, str(target))
                Path(bak).unlink(missing_ok=True)
        else:
            target.symlink_to(orig)


def build_execution_env(
    base_env: dict,
    ca_model_path: Path,
    record_dir: Path,
    origin_soc_version: str,
    resolved_cores: Optional[tuple],
    is_python_launch: bool,
    object_file: Optional[str] = None,
    model: str = "CA",
    cc_chip: int = 1,
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

    scope_filter = _find_or_build_scope_filter()
    if scope_filter is not None:
        # 过滤器先前置、helper 后前置；helper 自身不定义 dlopen 符号，
        # 二者顺序不影响 dlopen 插入（全局作用域中首个定义者生效）。
        env["LD_PRELOAD"] = _prepend_ld_preload(env.get("LD_PRELOAD", ""), scope_filter)
        print_info(f"dlopen scope filter enabled: {scope_filter}")

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

    # CC sim stack (mock HCCL + comm env) when --chip > 1.
    if cc_chip > 1:
        apply_cc_env(env, cc_chip, origin_soc_version)

    return env
