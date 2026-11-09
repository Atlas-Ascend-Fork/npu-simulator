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
Merge per-core trace_coreN.json files into multi-core traces.
"""

import gzip
import json
import os
import re

from npusim.core.public.comm import print_warn

SLOT_SUFFIX_RE = re.compile(r"_SLOT\d+$")

# Matches a per-core trace file: trace_core<N>.json or trace_core<N>.json.gz,
# optionally followed by a shard index (trace_core<N>_<k>.json[.gz]) as
# produced by report_path when one core's trace was split by --json-max-size.
TRACE_FILE_RE = re.compile(r"^trace_core(\d+)(?:_(\d+))?\.json(\.gz)?$")

# Extract numeric index from chip<N>_result / kernel_<N>_reports dir names.
_CHIP_INDEX_RE = re.compile(r"^chip(\d+)_result$")
_KERNEL_INDEX_RE = re.compile(r"^kernel_(\d+)_reports$")

# Contract-DB stem naming: <launch_id>_<kernel_name>_reports (e.g.
# 0003_entry_kernel_20_reports). The leading launch id keeps kernel
# indices unique within a chip.
_LAUNCH_ID_REPORTS_RE = re.compile(r"^(\d+).*_reports$")

# Match any *_reports directory (broader than _KERNEL_INDEX_RE, which only
# matches kernel_<N>_reports).
_KERNEL_REPORTS_RE = re.compile(r"^.*_reports$")

# pid template -- change this single line to alter the pid format
# (e.g. "core{n}.{family}" for lowercase, "CORE{n}.{family}" for the
# current uppercase style). All downstream uses (flow id encoding,
# sort-index assignment) derive from this, so no other code needs to
# change when the pid prefix format changes.
PID_TEMPLATE = "CORE{n}.{family}"

# pid template for global (all-chip all-kernel) merge. Embeds chip and
# kernel indices so that pids are globally unique across the entire run.
PID_TEMPLATE_GLOBAL = "CHIP{chip}.KERNEL{kernel}.CORE{n}.{family}"

# Fallback family when an event's original pid cannot be resolved in the
# family_map (e.g. a metadata-only process with no process_name). Using a
# neutral sentinel avoids hard-coding an assumption like "AIC".
FALLBACK_FAMILY = "UNKNOWN"

# Top-level trace output fields. Centralized so the two merge functions
# (merge_chip / merge_all) emit identical structure.
_PROFILING_TYPE = "op"
_SCHEMA_VERSION = 1
_DEFAULT_TIME_UNIT = "ns"


def _build_trace_output(merged_events, display_time_unit):
    """Build the top-level trace dict shared by merge_chip / merge_all."""
    return {
        "displayTimeUnit": display_time_unit or _DEFAULT_TIME_UNIT,
        "profilingType": _PROFILING_TYPE,
        "schemaVersion": _SCHEMA_VERSION,
        "traceEvents": merged_events,
    }


def strip_slot(name):
    """Remove a trailing _SLOTnn suffix from a thread/task name.

    BiProfRunner traces carry numeric ``tid`` values (int), where the
    suffix concept does not apply; non-string inputs are returned
    unchanged so the merge keeps the original format.
    """
    if not name or not isinstance(name, str):
        return name
    return SLOT_SUFFIX_RE.sub("", name)


def find_trace_files(core_dir, core_idx):
    """Locate all trace json files inside a ``core_<N>`` directory.

    Accepts uncompressed (``trace_core<N>.json``) and gzipped
    (``trace_core<N>.json.gz``) forms, plus the shard form
    ``trace_core<N>_<k>.json[.gz]`` written by ``report_path`` when one
    core's trace exceeded ``--json-max-size``. Shards are returned in
    ascending shard order so their events can be concatenated back into
    one per-core event list.

    Returns an empty list when no trace file is found.
    """
    try:
        names = os.listdir(core_dir)
    except OSError:
        return []

    def _order(fname):
        m = TRACE_FILE_RE.match(fname)
        if not m:
            return None
        # Prefer the file whose core index matches the directory, then sort
        # by shard index so trace_core0.json precedes trace_core0_1.json.
        return (0 if int(m.group(1)) == core_idx else 1, int(m.group(1)), int(m.group(2) or 0))

    matched = [(key, fname) for fname in names for key in [_order(fname)] if key is not None]
    if not matched:
        return []
    matched.sort()
    return [os.path.join(core_dir, fname) for _, fname in matched]


def load_trace_json(path):
    """Load a trace json file, transparently decompressing .gz.

    Raises FileNotFoundError if the path does not exist.
    """
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_core_trace_shards(trace_paths):
    """Load and concatenate one core's trace shards into a single event list.

    The engine splits a core's trace when it exceeds ``--json-max-size``;
    ``report_path`` keeps the shard index in the file name so every shard
    survives on disk. Merging must therefore read all of them, otherwise
    only the first (or whichever the glob picked) is merged.

    Returns ``(events, display_time_unit)``; the first shard carrying a
    ``displayTimeUnit`` wins.
    """
    events = []
    display_time_unit = None
    for path in trace_paths:
        data = load_trace_json(path)
        if display_time_unit is None:
            display_time_unit = data.get("displayTimeUnit")
        events.extend(data.get("traceEvents", []))
    return events, display_time_unit


def natural_key(s):
    """Pure-string natural sort key.

    Zero-pads every digit run to a fixed width and uppercases, so that
    "AIV10" sorts after "AIV2" ("AIV00000010" > "AIV00000002") and
    "aic" / "AIC" compare equal. Works for any input (no digit runs,
    empty string, mixed case, unknown prefixes) without raising.
    """
    return re.sub(r"\d+", lambda m: m.group(0).zfill(8), s).upper()


def make_pid(core_idx, family):
    """Build the merged-trace pid from a core index and a family token."""
    return PID_TEMPLATE.format(n=core_idx, family=family)


def build_family_map(trace_events):
    """Map original pid -> unit-family prefix (e.g. AIC / AIV0 / AIV1).

    Derived from the original process_name metadata events. The family
    token is the first underscore-delimited segment of process_name.
    Falls back to FALLBACK_FAMILY when process_name is empty/missing.
    """
    family_map = {}
    for e in trace_events:
        if e.get("ph") == "M" and e.get("name") == "process_name":
            pname = e.get("args", {}).get("name", "")
            if pname:
                family_map[e.get("pid")] = pname.split("_")[0]
            else:
                family_map[e.get("pid")] = FALLBACK_FAMILY
    return family_map


def _remap_core_events(core_events, core_idx, make_pid_func, family_map, pid_sort_index, emitted_pids, merged_events):
    """Remap one core's events into the merged output.

    Shared by :func:`merge_chip` and :func:`merge_all`. For each event:

    - Drop the original ``process_name`` metadata (the caller emits its
      own per-pid metadata).
    - Resolve the unit-family prefix from the original pid, then build
      the new pid via ``make_pid_func(core_idx, family)``.
    - The first time a new pid is seen, emit a ``process_sort_index``
      followed by a ``process_name`` metadata event.
    - Strip ``_SLOTnn`` from ``tid`` and ``args.task_id``.
    - Re-encode flow event (``s``/``f``) ids as ``"<new_pid>_<id>"`` so
      they are globally unique while keeping (s, f) pairs matched.

    Mutates ``emitted_pids`` and appends to ``merged_events`` in place.
    """
    for e in core_events:
        ph = e.get("ph")

        if ph == "M" and e.get("name") == "process_name":
            continue

        family = family_map.get(e.get("pid"), FALLBACK_FAMILY)
        new_pid = make_pid_func(core_idx, family)

        if new_pid not in emitted_pids:
            merged_events.append(
                {
                    "name": "process_sort_index",
                    "pid": new_pid,
                    "tid": "NA",
                    "ph": "M",
                    # 完整 family 列表在构建 pid_sort_index 时已知；事件里出现
                    # 未映射 pid（无 process_name 元数据）回退 UNKNOWN 时，惰性
                    # 分配下一个连续索引，而不是 KeyError 崩溃。
                    "args": {"sort_index": pid_sort_index.setdefault(new_pid, len(pid_sort_index) + 1)},
                }
            )
            merged_events.append(
                {
                    "name": "process_name",
                    "pid": new_pid,
                    "tid": "",
                    "ph": "M",
                    "args": {"name": new_pid},
                }
            )
            emitted_pids.add(new_pid)

        e["pid"] = new_pid

        tid = e.get("tid")
        if tid:
            e["tid"] = strip_slot(tid)

        args = e.get("args")
        if isinstance(args, dict) and "task_id" in args:
            args["task_id"] = strip_slot(args["task_id"])

        if ph in ("s", "f") and "id" in e:
            e["id"] = f"{new_pid}_{e['id']}"

        merged_events.append(e)


def _list_core_dirs(reports_root):
    """List ``core_<N>`` subdirectories under ``reports_root`` sorted by N.

    Raises FileNotFoundError if ``reports_root`` is not a directory or has
    no ``core_*`` subdirectory.
    """
    if not os.path.isdir(reports_root):
        raise FileNotFoundError(f"reports directory not found: {reports_root}")
    core_names = [
        d for d in os.listdir(reports_root) if d.startswith("core_") and os.path.isdir(os.path.join(reports_root, d))
    ]
    if not core_names:
        raise FileNotFoundError(f"no core_* directories under {reports_root}")
    # Sort by numeric core index so core_2 precedes core_10.
    core_names.sort(key=lambda d: int(d.split("_")[1]))
    return core_names


def _collect_core_families(reports_root, core_names):
    """First-pass: load each core's trace and collect its families.

    Returns a tuple ``(core_families, core_cache)``:

    - ``core_families``: ``{core_idx: [family, ...]}`` where families are
      natural-sorted (AIC < AIV < AIV0 < AIV1 < AIV2 < AIV10). Cores with
      no trace file are skipped.
    - ``core_cache``: ``{core_idx: (core_events, family_map, display_time_unit)}``
      holding the already-loaded data so the remap pass can reuse it
      without re-reading the file.
    """
    core_families = {}
    core_cache = {}
    for core_name in core_names:
        core_idx = int(core_name.split("_")[1])
        trace_paths = find_trace_files(os.path.join(reports_root, core_name), core_idx)
        if not trace_paths:
            continue
        core_events, display_time_unit = _load_core_trace_shards(trace_paths)
        family_map = build_family_map(core_events)
        core_families[core_idx] = sorted(set(family_map.values()), key=natural_key)
        core_cache[core_idx] = (core_events, family_map, display_time_unit)
    return core_families, core_cache


def merge_chip(chip_dir, kernel_dir, output_path):
    """Merge all core_* directories under chip_dir/results/<kernel_dir>/.

    Returns summary stats for verification.
    """
    reports_root = os.path.join(chip_dir, "results", kernel_dir)
    core_names = _list_core_dirs(reports_root)

    merged_events = []
    display_time_unit = None
    first_core_loaded = False

    # First pass: load each core's trace and collect its families.
    # The loaded data is cached so the remap pass below can reuse it
    # without re-reading the file.
    core_families, core_cache = _collect_core_families(reports_root, core_names)

    # Assign process_sort_index in the order:
    #   CORE0.<fam0>, CORE0.<fam1>, ..., CORE1.<fam0>, ...
    # skipping any (core, family) that does not actually exist, so the
    # assigned indices stay contiguous regardless of participation.
    pid_sort_index = {}
    sort_idx = 1
    for core_name in core_names:
        core_idx = int(core_name.split("_")[1])
        for family in core_families.get(core_idx, []):
            pid = make_pid(core_idx, family)
            pid_sort_index[pid] = sort_idx
            sort_idx += 1

    # Track which pids we have emitted metadata for.
    emitted_pids = set()

    for core_name in core_names:
        core_idx = int(core_name.split("_")[1])
        if core_idx not in core_cache:
            print_warn(f"missing trace file in {os.path.join(reports_root, core_name)}")
            continue

        core_events, family_map, dtu = core_cache[core_idx]

        if not first_core_loaded:
            display_time_unit = dtu
            first_core_loaded = True

        _remap_core_events(
            core_events,
            core_idx,
            make_pid,
            family_map,
            pid_sort_index,
            emitted_pids,
            merged_events,
        )

    output = _build_trace_output(merged_events, display_time_unit)

    with open(output_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    return {
        "output_path": output_path,
        "num_cores": len(core_names),
        "num_events": len(merged_events),
        "display_time_unit": display_time_unit,
        "num_pids": len(pid_sort_index),
    }


def _extract_chip_index(chip_dir):
    """Extract the numeric chip index from a chip_dir path.

    For multi-chip layout ``<report_dir>/chip<N>_result`` returns N.
    For single-chip fallback (chip_dir == report_dir, no wrapper) returns 0.
    """
    chip_name = os.path.basename(os.path.normpath(chip_dir))
    m = _CHIP_INDEX_RE.match(chip_name)
    if m:
        return int(m.group(1))
    return 0


def _extract_kernel_index(kernel_dir):
    """Extract the numeric kernel index from a ``*_reports`` directory name.

    Handles both the legacy ``kernel_<N>_reports`` naming and the
    contract-DB stem naming (``<launch_id>_<kernel>_reports``, e.g.
    ``0003_entry_kernel_20_reports``); the latter is keyed by the leading
    launch id so per-kernel merged outputs and merge_all cache keys stay
    unique. Falls back to 0 when the name carries no usable digits.
    """
    m = _KERNEL_INDEX_RE.match(kernel_dir)
    if m:
        return int(m.group(1))
    m = _LAUNCH_ID_REPORTS_RE.match(kernel_dir)
    if m:
        return int(m.group(1))
    return 0


def merge_all(report_dir, output_path):
    """Merge every chip's every kernel's per-core traces into one global trace.

    Discovers all (chip, kernel) tasks via :func:`discover_chip_tasks`,
    then merges all cores of all tasks into a single output file. Pids are
    globally unique and formatted as
    ``CHIP{chip}.KERNEL{kernel}.CORE{n}.{family}``.

    Returns summary stats for verification.
    """
    tasks = discover_chip_tasks(report_dir)
    if not tasks:
        raise FileNotFoundError(f"No chip*/results/*_reports or results/*_reports found under {report_dir}")

    # Build the ordered list of (chip_idx, kernel_idx, chip_dir, kernel_dir).
    task_infos = []
    for chip_dir, kernel_dir, _ in tasks:
        chip_idx = _extract_chip_index(chip_dir)
        kernel_idx = _extract_kernel_index(kernel_dir)
        task_infos.append((chip_idx, kernel_idx, chip_dir, kernel_dir))
    # Sort by (chip, kernel) so process order is deterministic.
    task_infos.sort(key=lambda t: (t[0], t[1]))

    # First pass: load each core's trace and collect families per
    # (chip, kernel, core). The loaded data is cached so the remap pass
    # below can reuse it without re-reading the file.
    # task_core_info[item] = (chip_idx, kernel_idx, chip_dir, kernel_dir,
    #                          [(core_idx, core_name), ...])
    # task_core_families[(chip_idx,kernel_idx,core_idx)] = sorted families
    # task_core_cache[(chip_idx,kernel_idx,core_idx)] = (events, family_map, dtu)
    task_core_info = []
    task_core_families = {}
    task_core_cache = {}
    for chip_idx, kernel_idx, chip_dir, kernel_dir in task_infos:
        reports_root = os.path.join(chip_dir, "results", kernel_dir)
        try:
            core_names = _list_core_dirs(reports_root)
        except FileNotFoundError as e:
            print_warn(f"{e}")
            continue
        core_families, core_cache = _collect_core_families(reports_root, core_names)
        cores_list = [(int(cn.split("_")[1]), cn) for cn in core_names if int(cn.split("_")[1]) in core_families]
        for core_idx, families in core_families.items():
            task_core_families[(chip_idx, kernel_idx, core_idx)] = families
            task_core_cache[(chip_idx, kernel_idx, core_idx)] = core_cache[core_idx]
        task_core_info.append((chip_idx, kernel_idx, chip_dir, kernel_dir, cores_list))

    # Assign global process_sort_index in the order
    #   CHIP0.KERNEL0.CORE0.<fam0>, CHIP0.KERNEL0.CORE0.<fam1>, ...,
    #   CHIP0.KERNEL0.CORE1.<fam0>, ...,
    #   CHIP0.KERNEL1.CORE0.<fam0>, ...,
    #   CHIP1.KERNEL0.CORE0.<fam0>, ...
    pid_sort_index = {}
    sort_idx = 1
    for chip_idx, kernel_idx, _chip_dir, _kernel_dir, cores_list in task_core_info:
        for core_idx, _ in cores_list:
            for family in task_core_families.get((chip_idx, kernel_idx, core_idx), []):
                pid = PID_TEMPLATE_GLOBAL.format(chip=chip_idx, kernel=kernel_idx, n=core_idx, family=family)
                pid_sort_index[pid] = sort_idx
                sort_idx += 1

    merged_events = []
    emitted_pids = set()
    display_time_unit = None
    first_core_loaded = False

    for chip_idx, kernel_idx, chip_dir, kernel_dir, cores_list in task_core_info:
        reports_root = os.path.join(chip_dir, "results", kernel_dir)

        def make_pid_func(core_idx, family):
            return PID_TEMPLATE_GLOBAL.format(chip=chip_idx, kernel=kernel_idx, n=core_idx, family=family)

        for core_idx, core_name in cores_list:
            cache_key = (chip_idx, kernel_idx, core_idx)
            if cache_key not in task_core_cache:
                print_warn(f"missing trace file in {os.path.join(reports_root, core_name)}")
                continue

            core_events, family_map, dtu = task_core_cache[cache_key]
            if not first_core_loaded:
                display_time_unit = dtu
                first_core_loaded = True

            _remap_core_events(
                core_events,
                core_idx,
                make_pid_func,
                family_map,
                pid_sort_index,
                emitted_pids,
                merged_events,
            )

    output = _build_trace_output(merged_events, display_time_unit)

    with open(output_path, "w") as f:
        json.dump(output, f, separators=(",", ":"))

    return {
        "output_path": output_path,
        "num_tasks": len(task_infos),
        "num_events": len(merged_events),
        "display_time_unit": display_time_unit,
        "num_pids": len(pid_sort_index),
    }


def _collect_tasks_for_chip(chip_dir):
    """Collect (chip_dir, kernel_reports_dir, output_path) tasks for one chip.

    Scans ``<chip_dir>/results/`` for any ``*_reports`` subdirectory.
    Output is written as ``<chip_dir>/trace_chip{N}_kernel{N}.json`` where
    the chip and kernel indices are extracted from the directory names.
    Returns an empty list if ``results/`` is missing or has no ``*_reports``.
    """
    results_dir = os.path.join(chip_dir, "results")
    if not os.path.isdir(results_dir):
        print_warn(f"no results/ dir in {chip_dir}, skipping")
        return []
    kernel_dirs = sorted(
        d
        for d in os.listdir(results_dir)
        if _KERNEL_REPORTS_RE.match(d) and os.path.isdir(os.path.join(results_dir, d))
    )
    if not kernel_dirs:
        print_warn(f"no *_reports dir under {results_dir}, skipping")
        return []
    chip_idx = _extract_chip_index(chip_dir)
    tasks = []
    for kernel_dir in kernel_dirs:
        kernel_idx = _extract_kernel_index(kernel_dir)
        out_name = f"trace_chip{chip_idx}_kernel{kernel_idx}.json"
        out_path = os.path.join(chip_dir, out_name)
        tasks.append((chip_dir, kernel_dir, out_path))
    return tasks


def discover_chip_tasks(report_dir):
    """Discover all (chip_dir, kernel_reports_dir, output_path) tasks.

    Multi-chip layout: auto-scans ``report_dir`` for any ``chip*_result``
    directory, and within each, any ``*_reports`` subdirectory under
    ``results/``. Output is written as
    ``<chip_dir>/trace_chip{N}_kernel{N}.json`` next to the chip.

    Single-chip fallback: when no ``chip*_result`` directory is found but
    ``report_dir/results/`` exists directly (single-card layout without a
    ``chip*_result`` wrapper), ``report_dir`` itself is treated as the
    chip directory (chip index 0) and the merged trace is written as
    ``report_dir/trace_chip0_kernel{N}.json``.

    This makes the script work for an arbitrary number of chips and
    kernel-report batches, and for single-card runs, without hard-coding
    names.
    """
    tasks = []
    chip_dirs = []
    for entry in sorted(os.listdir(report_dir)):
        if _CHIP_INDEX_RE.match(entry):
            chip_dirs.append(os.path.join(report_dir, entry))

    # Single-chip fallback: no chip*_result wrapper, but results/ exists
    # directly under report_dir (the layout produced by single-card
    # `cannsim report` runs).
    if not chip_dirs and os.path.isdir(os.path.join(report_dir, "results")):
        chip_dirs.append(report_dir)

    for chip_dir in chip_dirs:
        tasks.extend(_collect_tasks_for_chip(chip_dir))
    return tasks
