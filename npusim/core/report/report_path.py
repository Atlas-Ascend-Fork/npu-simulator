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
Report path utilities — file-system and path helpers for report generation.
"""

import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from npusim.core.public.soc_info import (
    DEFAULT_CORE_ID,
    _DEFAULT_MAX_CORES,
    parse_core_id as _shared_parse_core_id,
)
from npusim.core.report.report_strategy_base import SOC_TO_ARCH as _SOC_TO_ARCH

_MAX_CORES = _DEFAULT_MAX_CORES

# 多卡 per-chip DB 目录名：chip{N}（helper >= 0.8 布局）。
_CHIP_DB_DIR_RE = re.compile(r"^chip(\d+)$")


def _resolve_paths(export: str, output: str):
    """解析并校验导出/输出路径，返回 (export_abs, output_abs)，失败时两个元素均为 None。"""
    from npusim.core.public.comm import print_error

    export_abs = Path(export).resolve()
    output_abs = Path(output).resolve()

    if not export_abs.exists():
        print_error(f"Export directory does not exist: {export_abs}")
        return None, None

    if not output_abs.exists():
        output_abs.mkdir(parents=True, exist_ok=True)

    return export_abs, output_abs


def _find_instr_bin(export_abs: Path) -> Tuple[Optional[Path], bool]:
    """查找 instr.bin：export/instr.bin，其次 export/record/instr.bin。"""
    from npusim.core.public.comm import print_debug

    instr_bin = export_abs / "instr.bin"
    if instr_bin.exists():
        return (instr_bin, False)

    instr_bin_record = export_abs / "record" / "instr.bin"
    if instr_bin_record.exists():
        return (instr_bin_record, True)

    print_debug(f"Warning: instr.bin not found in {export_abs}")
    return (None, False)


def _find_log_ca(export_abs: Path) -> Optional[Path]:
    """instr.bin 不存在时的 fallback：export/log_ca 或 export/record/log_ca。"""
    log_ca_path = export_abs / "log_ca"
    if log_ca_path.exists():
        return log_ca_path

    log_ca_record = export_abs / "record" / "log_ca"
    if log_ca_record.exists():
        return log_ca_record

    return None


def _find_chip_db_dirs(export_abs: Path) -> List[Path]:
    """查找多卡 per-chip DB 目录（``chip{N}/`` 且内含 ``*.db``）。

    cannsim_helper >= 0.8 per-chip 模式（record ``--chip`` 与 ktest 均设
    ``CANNSIM_HELPER_PER_CHIP_DB=1``）把 ``record/chip{N}/{kernel}.db``
    写在 LAUNCH_DB_DIR 的 chip 子目录下。分别在 export 本身与
    ``export/record`` 查找，与 :func:`report._find_chip_instr_bins`
    双布局一致。helper 0.7.x 的平铺 ``chip{N}.db`` 是文件不是目录，
    不会误匹配，仍走单卡 DB 路径。

    Returns:
        List[Path]: 按 chip 编号升序排列的 chip 目录列表。
    """
    matches = []
    for base in (export_abs, export_abs / "record"):
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            m = _CHIP_DB_DIR_RE.match(entry.name)
            if m and entry.is_dir() and any(entry.glob("*.db")):
                matches.append((int(m.group(1)), entry))
    matches.sort(key=lambda item: item[0])
    return [entry for _, entry in matches]


def _find_cannsim_helper_db_input(export_abs: Path) -> Optional[Path]:
    """
    查找 cannsim_helper 生成的唯一受支持 SQLite DB 输入。

    支持单个 .db 文件、export/*.db、export/record/*.db，以及
    cannsim_helper_dbs 兼容目录。
    """
    if export_abs.is_file() and export_abs.suffix == ".db":
        return export_abs

    if export_abs.is_dir() and any(export_abs.glob("*.db")):
        return export_abs

    record_dir = export_abs / "record"
    if record_dir.is_dir() and any(record_dir.glob("*.db")):
        return record_dir

    for candidate in (
        export_abs / "cannsim_helper_dbs",
        export_abs / "record" / "cannsim_helper_dbs",
    ):
        if candidate.is_dir() and any(candidate.glob("*.db")):
            return candidate

    return None


def find_cannsim_helper_db_files(export_abs: Path) -> List[Path]:
    """展开 cannsim_helper DB 输入目录中的具体 SQLite DB 文件列表。"""
    db_input = _find_cannsim_helper_db_input(export_abs)
    if db_input is None:
        return []
    if db_input.is_file():
        return [db_input]
    return sorted(db_input.glob("*.db"))


def _build_biprof_args(
    output_abs: Path,
    input_db_path: Optional[Path] = None,
    soc_version: Optional[str] = None,
    core_id: Optional[str] = None,
    vf_report_json: bool = False,
    smart_pipes: bool = False,
    multi_chip: bool = False,
) -> List[str]:
    """构建 BiProfRunner report 子命令的完整参数列表。

    输入源仅支持 helper DB（``--input-db``）。instr.bin（``--binary-log``）
    与 log_ca（``--camodel-log``）输入已被两个 report 后端移除：BiProfRunner
    CLI 仅接受 ``--input-db``，prof_demo 适配器同样明确拒绝旧输入。
    """
    if input_db_path is None:
        raise ValueError("report input is required: a cannsim_helper contract DB")

    arch = _SOC_TO_ARCH.get(soc_version, "v100") if soc_version else "v100"
    args = [
        "report",
        "-a",
        arch,
        "-o",
        str(output_abs),
        "--input-db",
        str(input_db_path),
    ]

    if not multi_chip:
        args.extend(
            [
                "--scalar-ipc",
                "--vec-ipc",
            ]
        )
        if smart_pipes:
            args.append("--smart-pipes")

    args.extend(["--core-id", core_id or "0", "-j 8"])

    if vf_report_json:
        args.append("--vf-report-json")

    return args


def _parse_core_id(
    core_id: Optional[str],
    default: tuple = DEFAULT_CORE_ID,
) -> tuple:
    """解析 core_id 字符串为 int tuple；BiProfRunner 路径在校验通过后透传原始字符串。

    统一复用 :func:`npusim.core.public.soc_info.parse_core_id`，保证与 record
    子命令对 ``--core-id`` 的接受语义一致。report 场景下无 soc-version 上下文，
    沿用默认 32 核宽度。
    """
    return _shared_parse_core_id(core_id, _MAX_CORES, default=default)


# Regex: tracing report 文件名匹配，用于重命名为 trace_core{id}[_{part}].json[.gz]
# 匹配:
#   core_0_tracing_report_0.json / core_0_tracing_report_0.json.gz
#   core_0_smart_pipe_report_0.json / core_0_smart_pipe_report_0.json.gz
_TRACE_REPORT_RE = re.compile(r"^core_(\d+)_(tracing|smart_pipe)_report_(\d+)\.(json(?:\.gz)?)$")


def _rename_trace_if_match(filename: str) -> Optional[str]:
    """如果文件名匹配 tracing report，返回 dashboard 使用的 trace 文件名。

    引擎在一个 core 的 trace 超过 ``--json-max-size`` 时会把它切成
    ``core_{id}_{kind}_report_{k}`` 多个分片（见
    ``ChromeTracingDefault.divide_json``）。分片序号必须保留在目标名里：
    否则每个分片都会被改写成同一个 ``trace_core{id}.json``，写到同一路径，
    只剩下最后搬过去的那一片，其余分片的事件静默丢失。
    序号 0 仍命名为 ``trace_core{id}.json``（单分片时与旧名一致），
    其余分片为 ``trace_core{id}_{k}.json``。
    """
    m = _TRACE_REPORT_RE.match(filename)
    if m:
        suffix = "_smart_pipe" if m.group(2) == "smart_pipe" else ""
        part = "" if m.group(3) == "0" else f"_{m.group(3)}"
        return f"trace_core{m.group(1)}{suffix}{part}.{m.group(4)}"
    return None


_CORE_FILE_RE = re.compile(r"^(?:[A-Za-z]+_)?core_(\d+)_")

# Files that are not per-core and must stay in the output root.
_NON_CORE_FILES = frozenset(
    [
        "aicore_utilization.json",
    ]
)


def _is_pending_core_file(child: Path) -> bool:
    """判断文件是否是需要按 core 归类的 per-core 报告文件。

    ``*.npu-rep`` 归档除外:stage 3 会把它们写进 ``*_reports/`` 目录
    (先于本重组执行),即便文件名碰巧匹配 core 模式也不能被移动。
    """
    return (
        child.is_file()
        and child.name not in _NON_CORE_FILES
        and not child.name.endswith(".npu-rep")
        and _CORE_FILE_RE.match(child.name) is not None
    )


def _reorganize_reports_by_core(output_abs: Path) -> None:
    """将平铺的 kernel_*_reports/ 目录重组为按 core 分子的子目录结构。

    重组前（BiProfRunner 默认产出）::

        kernel_0_reports/
            core_0_icache_report.html
            core_0_tracing_report_0.json
            aicore_utilization.json

    重组后::

        kernel_0_reports/
            aicore_utilization.json          ← 保留在根目录
            core_0/
                core_0_icache_report.html
                trace_core0.json             ← tracing_report 重命名
            core_1/
                ...

    同一 kernel_*_reports/ 目录只会执行一次重组（通过检查是否已存在
    按 core 分子的子目录来判断）。重组失败时打印警告但不中断主流程。

    注意：支持嵌套的 results_xxx/ 等子目录结构。
    """
    from npusim.core.public.comm import print_info, print_warn, print_debug

    # Recursively find all *_reports/ directories. cannsim_helper DB-only
    # reports are named from launch id + kernel name and may not start
    # with "kernel_".
    kernel_dirs = sorted(d for d in output_abs.rglob("*") if d.is_dir() and d.name.endswith("_reports"))
    if not kernel_dirs:
        return

    for kernel_dir in kernel_dirs:
        pending_core_files = [child for child in kernel_dir.iterdir() if _is_pending_core_file(child)]

        # 检查是否已按 core 分子（避免重复操作），但仍允许修复根目录残留的
        # qi_core_N_* 等 per-core 文件。
        if not pending_core_files and any(re.match(r"^core_\d+$", child.name) for child in kernel_dir.iterdir()):
            print_debug(f"Already reorganized by core, skipping: {kernel_dir}")
            continue

        moved: Dict[str, int] = {}
        errors: List[str] = []

        for src in pending_core_files:
            base = src.name

            # 提取 core 目录名，如 "core_0" 从 "core_0_icache_report.html" 或
            # "qi_core_0_icache_utilization_report.html"。
            m = _CORE_FILE_RE.match(src.name)
            assert m, f"regex match failed for {src.name}"
            core_subdir = kernel_dir / f"core_{m.group(1)}"

            try:
                core_subdir.mkdir(exist_ok=True)
                # tracing_report 文件在移动时一步重命名为 trace_core{id}.json[.gz]
                dst_name = _rename_trace_if_match(base) or base
                shutil.move(str(src), str(core_subdir / dst_name))
                moved[core_subdir.name] = moved.get(core_subdir.name, 0) + 1
            except Exception as e:
                errors.append(f"  {src} → {core_subdir.name}: {e}")

        if moved:
            print_info(
                f"Reorganized reports by core in {kernel_dir.name}: "
                + ", ".join(f"{c} ({n} files)" for c, n in sorted(moved.items()))
            )
        if errors:
            print_warn(f"Some files could not be reorganized in {kernel_dir.name}:")
            for err in errors:
                print_warn(err)
