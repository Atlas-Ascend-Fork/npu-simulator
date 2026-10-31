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
Report module - CLI entry point for generating performance analysis reports.
"""

import sys
import json
import re
import shutil
import struct
import tempfile
import sqlite3
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import npusim.version as version
from npusim.core.public.comm import print_info, print_error, print_warn
from npusim.core.report.detections.instr_log_parser import (
    collect_device_pcs_from_record_db,
)
from npusim.core.report.detections.pc_source_mapper import load_source_mapper
from npusim.core.report.detections.sync_instruction_detection import (
    annotate_with_source as _sync_annotate,
    format_text as _sync_format_text,
    run as _sync_run,
)
from npusim.core.report.report_strategy_base import BIPROF_BACKEND_NAME, REORGANIZE_BACKENDS
from npusim.core.report.report_strategy_impl import _REPORT_CHAIN
from npusim.core.report.report_path import (
    _resolve_paths,
    _find_cannsim_helper_db_input,
    _find_instr_bin,
    _find_log_ca,
    _build_biprof_args,
    _parse_core_id,
    _reorganize_reports_by_core,
)

_CHIP_INSTR_RE = re.compile(r"^chip(\d*)_instr\.bin$")
_INSTR_ENTRY_SIZE = struct.calcsize("<QIIQB200s200s7x")


class _ReportContext(NamedTuple):
    """Namespaced report execution context."""

    export_abs: Path
    output_abs: Path
    input_db_path: Optional[Path]
    instr_bin_path: Optional[Path]
    log_ca_path: Optional[Path]
    core_ids: Tuple[int, ...]


def _check_helper_version(input_db_path: Path) -> bool:
    """校验 DB 的 helper_version 与本 wheel 版本精确匹配。

    cannsim 与 cannsim_helper 强制同版本配对，不匹配即拒绝出报告——
    版本错配会让 analyzer 读到不存在的列或已脱敏的内容，表现为跑通但
    结果错的静默故障。
    """
    dbs = [input_db_path] if input_db_path.is_file() else sorted(input_db_path.glob("*.db"))
    if not dbs:
        print_error(f"No .db file under {input_db_path}")
        return False

    for db in dbs:
        try:
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'helper_version'"
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error as e:
            print_error(f"Cannot read KernelInfo from {db.name}: {e}")
            return False

        if row is None:
            print_error(
                f"{db.name} has no helper_version in KernelInfo.\n"
                f"  It was produced by cannsim_helper older than {version.VERSION}, "
                "which is no longer supported.\n"
                "  Upgrade cannsim_helper and re-run `cannsim record`."
            )
            return False

        db_version = str(row[0])
        if db_version != version.VERSION:
            print_error(
                f"Version mismatch on {db.name}:\n"
                f"  cannsim_helper (produced the DB) : {db_version}\n"
                f"  cannsim wheel  (reading the DB)  : {version.VERSION}\n"
                "  The two must be the same version. Upgrade whichever is older."
            )
            return False

    return True


def _check_contract_version(input_db_path: Path) -> bool:
    """Require the exact helper/wheel data contract before any report reads."""
    dbs = [input_db_path] if input_db_path.is_file() else sorted(input_db_path.glob("*.db"))
    if not dbs:
        print_error(f"No .db file under {input_db_path}")
        return False
    for db in dbs:
        try:
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'contract_version'"
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error as exc:
            print_error(f"Cannot read contract_version from {db.name}: {exc}")
            return False
        if row is None or str(row[0]) != "7":
            actual = "missing" if row is None else str(row[0])
            print_error(
                f"Data contract mismatch on {db.name}: expected 7, got {actual}.\n"
                f"  Re-run `cannsim record` with cannsim_helper {version.VERSION}."
            )
            return False
    return True


def _chip_sort_key(path: Path) -> Tuple[int, str]:
    """Sort chip instr files by numeric chip id when available."""
    match = _CHIP_INSTR_RE.match(path.name)
    if match and match.group(1).isdigit():
        return (int(match.group(1)), path.name)
    return (sys.maxsize, path.name)


def _find_chip_instr_bins(export_abs: Path) -> List[Path]:
    """Find multi-card chip*_instr.bin files under export_abs or export_abs/record/."""
    result = sorted(export_abs.glob("chip*_instr.bin"), key=_chip_sort_key)
    if result:
        return result
    record_dir = export_abs / "record"
    if record_dir.exists():
        return sorted(record_dir.glob("chip*_instr.bin"), key=_chip_sort_key)
    return []


def _resolve_object_file(export_abs: Path, explicit: Optional[str]) -> Optional[str]:
    """解析用于 PC→源码行映射的目标文件路径。

    优先级：显式 ``--object-file`` > record 目录里的 ``.user-app`` 标记。
    ``.user-app`` 由 ``npusim record`` 写入（仅本地 binary/script 模式），记录用户
    可执行文件的绝对路径。这样 report 时即使不传 ``--object-file`` 也能自动回退，
    避免要求用户重复指定同一个产物。

    export 可能是 run 目录（含 ``record/`` 子目录）或 record 目录本身（含 instr.bin），
    两种布局都查找。
    """
    if explicit:
        return explicit
    candidates = [export_abs, export_abs / "record"]
    for d in candidates:
        marker = d / ".user-app"
        if marker.is_file():
            try:
                path = marker.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if not path:
                continue
            if not Path(path).is_file():
                print_warn(f"Recorded user app no longer exists; ignoring .user-app entry: {path}")
                continue
            return path
    return None


def _chip_result_name(chip_instr_bin: Path) -> str:
    """Convert chip0_instr.bin to chip0_result."""
    chip_name = chip_instr_bin.name[: -len("_instr.bin")]
    return f"{chip_name}_result"


def _prepare_single_chip_export(chip_instr_bin: Path, temp_dir: Path) -> None:
    """Expose one chip*_instr.bin as instr.bin for the existing single-card flow."""
    instr_bin = temp_dir / "instr.bin"
    remainder = chip_instr_bin.stat().st_size % _INSTR_ENTRY_SIZE
    if remainder:
        valid_size = chip_instr_bin.stat().st_size - remainder
        print_warn(f"{chip_instr_bin.name} has {remainder} trailing byte(s); ignoring incomplete binary log record.")
        with open(chip_instr_bin, "rb") as src, open(instr_bin, "wb") as dst:
            remaining = valid_size
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
        return

    try:
        instr_bin.symlink_to(chip_instr_bin)
    except OSError:
        shutil.copy2(chip_instr_bin, instr_bin)


def _find_chip_db_for_instr_bin(chip_instr_bin: Path, export_abs: Path) -> Optional[Path]:
    """Find the matching chipN.db for a chipN_instr.bin file.

    Searches export_abs and export_abs/record for a file named
    ``chip{N}.db`` where N matches the chip number in ``chip_instr_bin.name``.
    """
    match = _CHIP_INSTR_RE.match(chip_instr_bin.name)
    if not match:
        return None
    chip_num = match.group(1)
    db_name = f"chip{chip_num}.db"

    for search_dir in (export_abs, export_abs / "record"):
        db_path = search_dir / db_name
        if db_path.is_file():
            return db_path
    return None


def _prepare_single_chip_export_with_db(chip_instr_bin: Path, chip_db: Path, temp_dir: Path) -> None:
    """Expose one chip's instr.bin and helper DB for the single-card flow.

    Creates ``instr.bin`` (symlink or copy, same as _prepare_single_chip_export)
    and copies the helper DB into the temp dir so _find_cannsim_helper_db_input
    picks it up as a single-file input.
    """
    _prepare_single_chip_export(chip_instr_bin, temp_dir)

    db_dst = temp_dir / chip_db.name
    if not db_dst.exists():
        try:
            shutil.copy2(chip_db, db_dst)
        except OSError:
            shutil.copy2(chip_db, db_dst)


def _cached_object_file(cache_path: Path, explicit: Optional[str]) -> Optional[str]:
    """读取 PC 映射缓存中记录的目标文件；显式参数优先。"""
    if explicit is not None:
        return explicit
    if not cache_path.is_file():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        return cached.get("object_file") or None
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _collect_source_pcs(export_abs: Path) -> Dict[str, List[int]]:
    """收集源码映射所需的全部 device PC。"""
    try:
        return collect_device_pcs_from_record_db(export_abs)
    except OSError as e:
        print_warn(f"Source mapping: failed to read instruction dumps: {e}")
        return {}


def _prepare_source_mapper(cache_path: Path, cache_object: Optional[str], pcs: Dict[str, List[int]]):
    """加载或构建 PC→source mapper，并按需预生成持久化缓存。"""
    mapper = load_source_mapper(cache_path, cache_object)
    if mapper.available and not mapper.loaded_from_cache:
        # 提前符号化全部 device PC（含 DataCopy 的 MTE 库指令 inline 链），
        # 写入持久化缓存，后续 report 直接复用。
        mapper.prebuild(pcs)
        if pcs:
            mapper.save(cache_path)
            print_info(f"Source mapping: saved PC→source cache -> {cache_path}")
    return mapper


def _close_source_mapper(mapper) -> None:
    """释放 mapper 持有的临时设备 ELF；旧 mapper 或缓存 mapper 可为 no-op。"""
    close = getattr(mapper, "close", None)
    if callable(close):
        close()


def _has_record_db(export_abs: Path) -> bool:
    """Return whether sync detection can use a cannsim_helper record DB."""
    return _find_cannsim_helper_db_input(export_abs) is not None


def _run_detections(export_abs: Path, output_abs: Path, object_file: Optional[str] = None) -> bool:
    if not _has_record_db(export_abs):
        print_warn("sync detection skipped: no cannsim_helper record DB")
        (output_abs / "sync_instruction_detection_report.txt").write_text(
            "[sync_instruction_detection] Skipped: no cannsim_helper record DB.\n",
            encoding="utf-8",
        )
        return True

    cache_path = output_abs / "pc_source_map.json"
    cache_object = _cached_object_file(cache_path, object_file)
    device_pcs_by_kernel = _collect_source_pcs(export_abs)
    mapper = _prepare_source_mapper(cache_path, cache_object, device_pcs_by_kernel)

    try:
        hits = _sync_run(str(export_abs))
        _sync_annotate(hits, mapper)
    except (OSError, sqlite3.Error, ValueError) as e:
        print_error(f"sync_instruction_detection failed: {e}")
        _out = output_abs / "sync_instruction_detection_report.txt"
        _out.write_text(f"[sync_instruction_detection] detection failed: {e}\n", encoding="utf-8")
        return False
    finally:
        _close_source_mapper(mapper)

    _out = output_abs / "sync_instruction_detection_report.txt"
    try:
        _out.write_text(_sync_format_text(hits), encoding="utf-8")
        print_info(f"sync_instruction_detection: {len(hits)} violation(s) -> {_out}")
    except OSError as e:
        print_error(f"sync_instruction_detection failed: {e}")
        _out.write_text(f"[sync_instruction_detection] detection failed: {e}\n", encoding="utf-8")
        return False

    # 检测命中是报告结果，不是 report 工具本身的执行失败；只有
    # 检测流程异常时才让 generate_report 返回 False。
    return True


def generate_report(
    export: str,
    output: Optional[str] = None,
    core_id: Optional[str] = None,
    object_file: Optional[str] = None,
    save_tmp_files: bool = False,
    export_vf: bool = False,
    smart_pipes: bool = False,
    multi_chip: bool = False,
    merge_trace: bool = False,
) -> bool:
    """
    生成性能报告（BiProfRunner）。

    输入源两种解析方式并存：
    - helper DB（数据合同，后续主路径）：校验 helper_version / contract_version
    - instr.bin / log_ca（当前多卡与无 DB 场景）

    Args:
        export (str): 模拟执行结果目录路径（如 ./output/sim）
        output (str, optional): 输出目录路径（如 ./output/reports）。
            默认归档到 export 目录下。
        core_id (str, optional): 核心 ID 范围（如 '0-2,5' 或 'all'）。
        object_file (str, optional): 设备目标文件路径（BiProfRunner 路径使用）
        save_tmp_files (bool): 保留 biprof 中间 DB 文件。
        export_vf (bool): 生成 VF report JSON 到对应 kernel_*_reports/ 目录下。
        smart_pipes (bool): 额外生成 smart pipe tracing JSON。
        multi_chip (bool): 多卡 report 模式，控制 BiProf 参数裁剪。
        merge_trace (bool): 生成完所有核的流水图后同时生成两种合并产物：
            每个 chip 每个 kernel 各自合并（pid=``CORE{n}.{family}``，
            产物 ``<chip_dir>/trace_chip{N}_kernel{N}.json``）以及
            所有 chip 所有 kernel 全局合并（pid=``CHIP{c}.KERNEL{k}.CORE{n}.{family}``，
            产物 ``<report_dir>/trace_allchips.json``）。
            仅在单卡模式（multi_chip=False）且 core_id 非空时于此触发；
            多卡模式由上层 ``generate_multi_chip_report`` 在循环结束后统一合并。

    输出（helper DB 路径 + BiProf 模块后端成功时）除 ``results/`` 下各
    ``*_reports`` 目录外，还会为每个契约 DB 生成一个 ``{db_stem}.npu-rep``
    归档（DB 表 CSV + manifest.json + 该 kernel 的报告 JSON），位于该 DB
    对应的 ``results/{...}_reports/`` 目录内；无报告目录时回退到 report
    输出根目录。由 :mod:`npusim.core.report.npu_rep_export` 以尽力而为
    方式打包。

    Returns:
        bool: 成功返回 True，失败返回 False
    """
    print_info("Generating performance report...")

    context = _prepare_report_context(export, output, core_id)
    if context is None:
        return False
    export_abs, output_abs, input_db_path, instr_bin_path, log_ca_path, core_ids = context
    success = _run_report_backend(
        output_abs,
        export_abs,
        input_db_path=input_db_path,
        instr_bin_path=instr_bin_path,
        log_ca_path=log_ca_path,
        core_id=core_id,
        core_ids=core_ids,
        export_vf=export_vf,
        smart_pipes=smart_pipes,
        multi_chip=multi_chip,
    )
    if success:
        _generate_aggregate_report(output_abs)

    # ⑥ 多核流水图合并（仅单卡模式；多卡模式由 generate_multi_chip_report
    #    在循环结束后统一合并，避免重复触发）。需显式带 -n 才执行。
    should_merge = merge_trace and core_id and not multi_chip
    if success and should_merge:
        _merge_core_traces(output_abs)

    detections_ok = _run_detections(export_abs, output_abs, object_file=object_file)
    success = success and detections_ok
    return success


def _prepare_report_context(
    export: str,
    output: Optional[str],
    core_id: Optional[str],
) -> Optional[_ReportContext]:
    """解析路径、输入源和 core_id；校验失败时返回 None。"""
    # 未指定 output 时默认归档到 export/report 目录
    if output is None:
        output = str(Path(export) / "report")

    # ① 解析并校验路径
    paths = _resolve_paths(export, output)
    if paths[0] is None:
        return None
    export_abs, output_abs = paths

    # ② 查找输入源：helper DB 优先，否则 fallback 到 instr.bin / log_ca。
    input_db_path, instr_bin_path, log_ca_path = _resolve_report_input(export_abs)
    if input_db_path is None and instr_bin_path is None and log_ca_path is None:
        return None
    if input_db_path is not None and not _validate_helper_contract(input_db_path):
        return None

    # ③ 解析 core_id，校验通过后透传给 BiProfRunner
    try:
        core_ids = _parse_core_id(core_id)
    except ValueError as e:
        print_error(f"Invalid --core-id: {e}")
        return None
    return _ReportContext(
        export_abs=export_abs,
        output_abs=output_abs,
        input_db_path=input_db_path,
        instr_bin_path=instr_bin_path,
        log_ca_path=log_ca_path,
        core_ids=core_ids,
    )


def _resolve_report_input(export_abs: Path) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """选择 helper DB / instr.bin / log_ca 中可用的报告输入源。"""
    input_db_path = _find_cannsim_helper_db_input(export_abs)
    if input_db_path is not None:
        print_info(f"Found cannsim_helper DB input: {input_db_path}")
        return input_db_path, None, None

    instr_bin_path, _ = _find_instr_bin(export_abs)
    if instr_bin_path is not None:
        print_info(f"Found instr.bin: {instr_bin_path}")
        return None, instr_bin_path, None

    log_ca_path = _find_log_ca(export_abs)
    if log_ca_path is not None:
        print_info(f"Found log_ca directory: {log_ca_path}")
        return None, None, log_ca_path

    print_error("No log source found in export directory. Need a cannsim_helper DB, instr.bin, or log_ca.")
    return None, None, None


def _validate_helper_contract(input_db_path: Path) -> bool:
    """helper DB 必须与当前 wheel 使用完全一致的数据合同。"""
    return _check_helper_version(input_db_path) and _check_contract_version(input_db_path)


def _run_report_backend(
    output_abs: Path,
    export_abs: Path,
    *,
    input_db_path: Optional[Path],
    instr_bin_path: Optional[Path],
    log_ca_path: Optional[Path],
    core_id: Optional[str],
    core_ids,
    export_vf: bool,
    smart_pipes: bool,
    multi_chip: bool,
) -> bool:
    """构建 BiProf 参数并执行报告后处理流水。"""
    # ④ 构建参数 → 执行 BiProfRunner
    biprof_args = _build_biprof_args(
        output_abs,
        input_db_path=input_db_path,
        instr_bin_path=instr_bin_path,
        log_ca_path=log_ca_path,
        core_id=core_id,
        vf_report_json=export_vf,
        smart_pipes=smart_pipes,
        multi_chip=multi_chip,
    )
    backend, success = _REPORT_CHAIN.execute(
        biprof_args,
        export_abs=export_abs,
        output_abs=output_abs,
        core_ids=core_ids,
    )

    # ⑤ Stage 3: 每个契约 DB 导出 CSV 并打包 .npu-rep(BiProf 后端专属,尽力而为)。
    # 必须先于 ⑥ 执行:打包的是引擎原始输出,trace JSON 保留引擎命名
    # (core_{n}_tracing_report_{i}.json)——.npu-rep 消费方按原始文件名识别;
    # ⑥ 的重组/重命名(trace_core{n}.json)只服务于磁盘上的 dashboard 布局。
    if success and backend == BIPROF_BACKEND_NAME and input_db_path is not None:
        _export_npu_rep(output_abs, input_db_path)

    # ⑥ 后处理产物目录结构(dashboard 布局:core_N/ 子目录 + trace_coreN.json 重命名)
    if success and backend in REORGANIZE_BACKENDS:
        _reorganize_reports_by_core(output_abs)
    return success


def _export_npu_rep(output_abs: Path, input_db_path: Path) -> None:
    """Stage 3 best-effort: CSV export + .npu-rep packing per contract DB.

    失败只打印告警,不阻断报告主流程。
    """
    try:
        from npusim.core.report.npu_rep_export import export_npu_rep

        export_npu_rep(output_abs, input_db_path)
    except Exception as e:  # noqa: BLE001 -- best-effort post-processing
        print_warn(f"npu-rep export skipped: {e}")


def _generate_aggregate_report(output_abs: Path) -> None:
    """生成聚合 SUMMARY/index 报告；失败只降级提示，不阻断主报告。"""
    try:
        from npusim.dashboard import generate_summary_index

        results_dir = Path(output_abs)
        if results_dir.is_dir():
            print_info("Generating aggregated index.html...")
            if generate_summary_index(results_dir):
                print_info("Aggregated index.html generated successfully!")
            else:
                print_info("Aggregated index.html generation had warnings.")
    except Exception as e:
        print_info(f"Skipped aggregated HTML: {e}")


def generate_multi_chip_report(
    export: str,
    output: Optional[str] = None,
    core_id: Optional[str] = None,
    object_file: Optional[str] = None,
    export_vf: bool = True,
    smart_pipes: bool = False,
    merge_trace: bool = False,
) -> bool:
    """Generate reports for chip*_instr.bin files under export.

    When ``merge_trace`` is True and ``core_id`` is provided, all
    per-core traces are merged after the per-chip report loop finishes:
    each chip per kernel is merged into ``trace_chip{N}_kernel{N}.json``
    and all chips/kernels are merged into one global ``trace_allchips.json``.
    """
    export_abs = Path(export).resolve()
    if not export_abs.exists():
        print_error(f"Export directory does not exist: {export_abs}")
        return False

    chip_instr_bins = _find_chip_instr_bins(export_abs)
    if not chip_instr_bins:
        print_error(f"No chip*_instr.bin files found in export directory: {export_abs}")
        return False

    output_root = Path(output).resolve() if output else export_abs / "report"
    output_root.mkdir(parents=True, exist_ok=True)

    print_info("Detected multi-card analysis mode.")
    print_info("Found chip instruction logs: " + ", ".join(chip_instr_bin.name for chip_instr_bin in chip_instr_bins))

    all_ok = True
    for chip_instr_bin in chip_instr_bins:
        chip_output = output_root / _chip_result_name(chip_instr_bin)
        chip_db = _find_chip_db_for_instr_bin(chip_instr_bin, export_abs)
        if chip_db is not None:
            print_info(f"Generating report for {chip_instr_bin.name} with helper DB {chip_db.name} -> {chip_output}")
        else:
            print_info(f"Generating report for {chip_instr_bin.name} -> {chip_output}")
        with tempfile.TemporaryDirectory(prefix=f"{chip_instr_bin.stem}_") as tmp:
            temp_export = Path(tmp)
            if chip_db is not None:
                _prepare_single_chip_export_with_db(
                    chip_instr_bin,
                    chip_db,
                    temp_export,
                )
            else:
                _prepare_single_chip_export(chip_instr_bin, temp_export)
            ok = generate_report(
                export=str(temp_export),
                output=str(chip_output),
                core_id=core_id,
                object_file=object_file,
                export_vf=export_vf,
                smart_pipes=smart_pipes,
                multi_chip=True,
            )
            all_ok = all_ok and ok

    # ⑥ 多核流水图合并：所有卡的所有核流水图生成完毕后，同时生成
    #    per-chip 与全集合并产物。需显式带 -n 才执行。
    if all_ok and merge_trace and core_id:
        _merge_core_traces(output_root)

    return all_ok


def _merge_core_traces(report_dir) -> None:
    """同时生成两种多核合并产物。best-effort：失败打印 warning 不中断主流程。

    1. 每个 chip 每个 kernel 各自合并，pid=``CORE{n}.{family}``，
       产物 ``<chip_dir>/trace_chip{N}_kernel{N}.json``；
    2. 所有 chip 所有 kernel 全局合并，pid=``CHIP{c}.KERNEL{k}.CORE{n}.{family}``，
       产物 ``<report_dir>/trace_allchips.json``。
    """
    from npusim.core.report.report_trace_merged import (
        discover_chip_tasks,
        merge_chip,
        merge_all,
    )

    # (1) per-chip per-kernel merge
    tasks = discover_chip_tasks(str(report_dir))
    if not tasks:
        print_warn(f"No trace reports found to merge under {report_dir}")
        return

    for chip_dir, kernel_dir, out_path in tasks:
        try:
            print_info(f"Merging multi-core traces for {chip_dir} ({kernel_dir})...")
            stats = merge_chip(chip_dir, kernel_dir, out_path)
            print_info(
                f"  -> {stats['output_path']} "
                f"(cores={stats['num_cores']}, pids={stats['num_pids']}, "
                f"events={stats['num_events']})"
            )
        except Exception as e:
            print_warn(f"Failed to merge traces for {chip_dir}/{kernel_dir}: {e}")

    # (2) global all-chip all-kernel merge
    out_path = str(Path(report_dir) / "trace_allchips.json")
    try:
        print_info("Merging all chip/kernel traces into one global trace...")
        stats = merge_all(str(report_dir), out_path)
        print_info(
            f"  -> {stats['output_path']} "
            f"(tasks={stats['num_tasks']}, pids={stats['num_pids']}, "
            f"events={stats['num_events']})"
        )
    except Exception as e:
        print_warn(f"Failed to merge all traces: {e}")


def report(export, output, core_id, object_file=None, export_vf=True, smart_pipes=False, merge_trace=False) -> bool:
    """
    \\b
    Command-line interface for generating performance reports.
    VF report JSON is always included (full report).

    分流：
    - 目录里有 ``chip*_instr.bin`` → 当前多卡 instr.bin 路径
    - 否则走单卡：helper DB（数据合同）或 instr.bin / log_ca

    BiProf 模块后端成功时，每个契约 DB 额外产出 ``{db_stem}.npu-rep`` 归档
    （位于该 DB 对应的 ``results/{...}_reports/`` 目录内，无报告目录时回退
    到 report 输出根目录）。

    \\b
    Examples:
        $ npusim report -e ./simulation_results -o ./output
        $ npusim report -e ./output/sim -o ./output/reports -n all
        $ npusim report -e ./output/sim -o ./output/reports -n all -m

    Returns:
        bool: True if report generation succeeded, False otherwise.
    """
    export_abs = Path(export).resolve()

    # 未显式指定 --object-file 时，回退到 record 目录里 .user-app 标记记录的
    # 用户可执行文件路径（由 npusim record 写入），用于 PC→源码行映射。
    object_file = _resolve_object_file(export_abs, object_file)

    if export_abs.exists() and _find_chip_instr_bins(export_abs):
        return generate_multi_chip_report(
            export=export,
            output=output,
            core_id=core_id,
            object_file=object_file,
            export_vf=export_vf,
            smart_pipes=smart_pipes,
            merge_trace=merge_trace,
        )

    if _find_cannsim_helper_db_input(export_abs):
        print_info("Detected cannsim_helper DB analysis mode.")
    else:
        print_info("Detected instr.bin / log_ca analysis mode.")
    return generate_report(
        export=export,
        output=output,
        core_id=core_id,
        object_file=object_file,
        export_vf=export_vf,
        smart_pipes=smart_pipes,
        merge_trace=merge_trace,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-e",
        "--export",
        required=True,
        help="Folder containing the simulation model execution results",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Folder where reports will be generated (default: same as export directory)",
    )
    parser.add_argument(
        "-n",
        "--core-id",
        help="Core ID(s) to use. Support formats: all, 0-2,12-14, 5",
    )
    parser.add_argument(
        "--smart-pipes",
        action="store_true",
        help="Also generate smart pipe tracing JSON",
    )
    parser.add_argument(
        "-m",
        "--merge-trace",
        action="store_true",
        default=False,
        help="Merge per-core traces: generate both per-chip per-kernel "
        "trace_chip{N}_kernel{N}.json and a global trace_allchips.json "
        "(requires -n).",
    )

    args = parser.parse_args()
    sys.exit(
        0
        if report(
            args.export, args.output, args.core_id, None, smart_pipes=args.smart_pipes, merge_trace=args.merge_trace
        )
        else 1
    )
