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

"""Artifact archiving helpers for cannsim record."""

import shutil
from pathlib import Path
from typing import List, Optional, Sequence

from npusim.core.public.comm import print_error, print_info, print_warn


# instr 产物后缀：所有以 instr.bin 结尾的文件都视为仿真产物，需归档。
INSTR_BIN_SUFFIX = "instr.bin"


def archive_assets(
    record_dir: Optional[Path],
    asset_names: Sequence[str],
    asset_roots,
    on_fail_delete: bool = False,
) -> bool:
    """把 fallback 产物从候选目录搬到 ``record_dir``。

    ``asset_roots`` 是一组按优先级排列的候选目录（通常至少包含执行 cwd），
    每个 asset 取第一个存在的来源搬到 archive；同一来源已搬过的 asset 不再重复处理。
    """
    record_dir_resolved = Path(record_dir).resolve() if record_dir else None
    normalized_roots: List[Path] = []
    seen_roots = set()
    for root in asset_roots or []:
        if root is None:
            continue
        try:
            root_abs = Path(root).resolve()
        except Exception:
            continue
        if record_dir_resolved is not None and root_abs == record_dir_resolved:
            continue
        if root_abs in seen_roots:
            continue
        seen_roots.add(root_abs)
        normalized_roots.append(root_abs)

    has_target = bool(record_dir and Path(record_dir).exists())

    if not has_target:
        if on_fail_delete:
            print_warn("Target directory unavailable; cleaning residual assets.")
        else:
            print_warn("Target directory unavailable; leaving assets in place.")
            return True

    overall_ok = True
    for asset_name in asset_names:
        # asset_name 可含通配符（如 ``stars_log*_task.dump``）：先在候选 root 下展开成
        # 具体文件再搬运；同一文件只处理一次（跨 root 去重，优先列在前面的 root）。
        is_glob = "*" in asset_name or "?" in asset_name
        if is_glob:
            resolved: List[Path] = []
            seen_matches: set = set()
            for root in normalized_roots:
                for match in sorted(root.glob(asset_name)):
                    try:
                        match_abs = match.resolve()
                    except Exception:
                        continue
                    if match_abs in seen_matches:
                        continue
                    seen_matches.add(match_abs)
                    resolved.append(match)
            asset_paths = resolved
        else:
            src_path = next(
                (root / asset_name for root in normalized_roots if (root / asset_name).exists()),
                None,
            )
            asset_paths = [src_path] if src_path is not None else []

        for src_path in asset_paths:
            if src_path is None or not src_path.exists():
                continue
            try:
                asset_label = src_path.name
                if has_target:
                    dst_path = Path(record_dir) / asset_label
                    if dst_path.exists():
                        if dst_path.is_dir():
                            shutil.rmtree(dst_path)
                        else:
                            dst_path.unlink()
                    shutil.move(str(src_path), str(dst_path))
                else:
                    if src_path.is_dir():
                        shutil.rmtree(src_path)
                    else:
                        src_path.unlink()
            except Exception as e:
                overall_ok = False
                action = "archive" if has_target else "cleanup"
                print_error(f"Failed to {action} {asset_name}: {e}")

    return overall_ok


def cleanup_internal_dirs(record_dir: Optional[Path]) -> None:
    """清理 archive 中对终端用户无意义的中间产物目录。"""
    if not record_dir or not Path(record_dir).exists():
        return
    for name in ("log", "log_ca", "run_log"):
        path = Path(record_dir) / name
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception as e:
            print_warn(f"Failed to remove {path}: {e}")


def archive_instr_bin_glob(
    target_dir: Path,
    asset_roots,
    suffix: str = INSTR_BIN_SUFFIX,
) -> bool:
    """把所有以 ``instr.bin`` 结尾的文件从 ``asset_roots`` 搬到 ``target_dir``。

    与 :func:`archive_assets` 的差异：
    - archive_assets 按精确文件名匹配；本函数按后缀 glob 匹配，
      适配 sim 现在生成的多核 instr 产物（如 ``core0_instr.bin``）。
    - 只搬文件，不搬目录。

    Args:
        target_dir: 归档目标目录（必须已存在）。
        asset_roots: 候选来源目录列表，按优先级排列。
        suffix: 文件名后缀，默认 ``instr.bin``。

    Returns:
        True 表示全部搬运成功；False 表示至少一个失败（已尽力搬其余文件）。
    """
    target = Path(target_dir).resolve()
    if not target.exists():
        print_warn(f"Target directory unavailable; leaving instr.bin assets in place: {target}")
        return True

    normalized_roots = []
    seen = set()
    for root in asset_roots or []:
        if root is None:
            continue
        try:
            root_abs = Path(root).resolve()
        except Exception:
            continue
        if root_abs == target:
            continue
        if root_abs in seen:
            continue
        seen.add(root_abs)
        normalized_roots.append(root_abs)

    overall_ok = True
    moved = 0
    for root in normalized_roots:
        for src in root.glob(f"*{suffix}"):
            if not src.is_file():
                continue
            dst = target / src.name
            try:
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception as e:
                overall_ok = False
                print_error(f"Failed to archive {src.name}: {e}")

    if moved:
        print_info(f"Archived {moved} *{suffix} file(s) to {target}")
    return overall_ok


# cannsim_helper per-chip DB 产物后缀。
CHIP_DB_PATTERN = "chip*.db"


def archive_helper_dbs(
    target_dir: Path,
    asset_roots,
) -> bool:
    """把 ``chip*.db`` 文件从 ``asset_roots`` 搬到 ``target_dir``。

    与 :func:`archive_instr_bin_glob` 对称，但匹配 ``chip*.db`` glob，
    专用于 cannsim_helper 0.7.x per-chip DB 产物。ktest 模式下 helper 把
    ``chip{N}.db`` 写在 ``CANNSIM_HELPER_LAUNCH_DB_DIR``（= record_dir），
    但如果用户手动设了别的目录或 camodel cwd 产生了 db，这里做 fallback 搬运。

    跳过与 target_dir 相同的 root，避免在原地将文件搬给自己。

    Args:
        target_dir: 归档目标目录（必须已存在）。
        asset_roots: 候选来源目录列表，按优先级排列。

    Returns:
        True 表示全部搬运成功；False 表示至少一个失败。
    """
    target = Path(target_dir).resolve()
    if not target.exists():
        print_warn(f"Target directory unavailable; leaving chip DB assets in place: {target}")
        return True

    normalized_roots = []
    seen = set()
    for root in asset_roots or []:
        if root is None:
            continue
        try:
            root_abs = Path(root).resolve()
        except Exception:
            continue
        if root_abs == target:
            continue
        if root_abs in seen:
            continue
        seen.add(root_abs)
        normalized_roots.append(root_abs)

    overall_ok = True
    moved = 0
    for root in normalized_roots:
        for src in root.glob(CHIP_DB_PATTERN):
            if not src.is_file():
                continue
            dst = target / src.name
            try:
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))
                moved += 1
            except Exception as e:
                overall_ok = False
                print_error(f"Failed to archive {src.name}: {e}")

    if moved:
        print_info(f"Archived {moved} chip DB file(s) to {target}")
    return overall_ok
