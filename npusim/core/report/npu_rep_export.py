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

"""Stage 3 of report generation: CSV export + .npu-rep packing per contract DB.

For every contract DB processed by the BiProf report engine this module:

1. exports every non-empty table/view of the DB to CSV (+ ``manifest.json``),
2. copies the JSON reports of that DB's ``*_reports`` directory into the
   staging folder (``.json.gz`` files are unpacked on the fly),
3. packs the staging folder into ``{db_stem}.npu-rep`` **inside that DB's
   reports directory** (``{output}/results/{...}_reports/{db_stem}.npu-rep``);
   when no reports directory exists the archive falls back to the report
   output root (``{output}/{db_stem}.npu-rep``).

Stage 3 runs on the RAW engine output, i.e. before
``_reorganize_reports_by_core`` renames trace JSONs
(``core_{n}_tracing_report_{i}.json`` -> ``trace_core{n}.json``) and moves
them into ``core_N/`` subdirectories. External .npu-rep consumers identify
report files by the engine-original names, so the packing order is a
compatibility contract: the archive must keep the engine names while the
on-disk dashboard layout gets the reorganized/renamed ones.

The staging folder ``{output}/npu-rep`` is transient: it is wiped before each
DB and removed after the last pack, so the persistent artifacts are exactly
one ``.npu-rep`` archive per contract DB.

Everything here is best-effort: a failure while processing one DB prints a
warning and continues with the remaining DBs; it never fails the report run
itself. Only the Python standard library is required, so this module stays
importable in minimal environments (it is exercised on the BiProf backend
path only).
"""

import gzip
import re
import shutil
import sqlite3
from pathlib import Path
from typing import List, Optional, Tuple

from npusim.core.public.comm import print_info, print_warn
from npusim.prof.tools.export_csv import export_database
from npusim.prof.tools.pack_npu_rep import RepError, collect_entries, encode_rep

_STAGING_DIR_NAME = "npu-rep"
_RESULTS_DIR_NAME = "results"


def _expand_db_list(input_db_path: Path) -> List[Path]:
    """Expand a helper-DB input (single file or directory) into a sorted DB list."""
    if input_db_path.is_file():
        return [input_db_path]
    return sorted(input_db_path.glob("*.db"))


def _safe_report_component(value: str) -> str:
    """Sanitize a kernel name for the reports directory name.

    Must stay in sync with ``npusim.prof.src.backend.reporters._safe_report_component``.
    """
    value = value.strip() or "kernel"
    value = re.sub(r"[^\w.\-+=:@]+", "_", value)
    return value[:240] or "kernel"


def _read_kernel_info(db_path: Path, index: int) -> Tuple[int, str]:
    """Read launch_id/name from KernelInfo, mirroring the engine's fallbacks.

    The engine falls back to the sorted-iteration index when launch_id is
    missing/unparseable and to the DB file stem when the kernel name is
    missing; stage 3 iterates the same sorted list, so the same fallbacks
    reproduce the engine's ``{launch:04d}_{name}_reports`` naming.
    """
    launch_id = index
    kernel_name = db_path.stem
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'launch_id'").fetchone()
            if row is not None and str(row[0]).strip():
                launch_id = int(str(row[0]))
            row = con.execute("SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr = 'name'").fetchone()
            if row is not None and str(row[0]).strip():
                kernel_name = str(row[0])
        finally:
            con.close()
    except (sqlite3.Error, ValueError):
        pass
    return launch_id, kernel_name


def _find_reports_dir(output_abs: Path, db_path: Path, index: int) -> Optional[Path]:
    """Locate the report directory that belongs to ``db_path``.

    Candidate names, in order: the DB-stem based layout produced for
    cannsim_helper DB files (``{stem}_reports``), the prof_demo layout
    (``kernel_{stem}_reports``), and the engine's KernelInfo-derived naming.
    """
    results_root = Path(output_abs) / _RESULTS_DIR_NAME
    launch_id, kernel_name = _read_kernel_info(db_path, index)
    candidates = (
        f"{db_path.stem}_reports",
        f"kernel_{db_path.stem}_reports",
        f"{launch_id:04d}_{_safe_report_component(kernel_name)}_reports",
    )
    for candidate in candidates:
        path = results_root / candidate
        if path.is_dir():
            return path
    return None


def _copy_report_jsons(reports_dir: Path, staging_dir: Path) -> int:
    """Copy report JSONs into the staging folder, gunzipping on the fly.

    File names are preserved as the engine wrote them (e.g.
    ``core_0_tracing_report_0.json``): .npu-rep consumers key on the
    engine-original names. The recursive glob is generality only — the raw
    engine output this normally runs on is flat.
    """
    copied = 0
    sources = sorted(list(reports_dir.rglob("*.json")) + list(reports_dir.rglob("*.json.gz")))
    for src in sources:
        if src.suffix == ".gz":
            dst = staging_dir / src.with_suffix("").name
            with gzip.open(src, "rb") as gz, open(dst, "wb") as out:
                shutil.copyfileobj(gz, out)
        else:
            shutil.copy(src, staging_dir)
        copied += 1
    return copied


def _pack_one_db(db_path: Path, index: int, output_abs: Path, staging_dir: Path) -> None:
    """CSV export + JSON copy + pack for a single contract DB."""
    if staging_dir.exists() or staging_dir.is_symlink():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    export_database(str(db_path), str(staging_dir))

    reports_dir = _find_reports_dir(output_abs, db_path, index)
    if reports_dir is None:
        print_warn(
            f"No reports directory found for {db_path.name}; "
            "packing CSV data only (archive falls back to the output root)"
        )
    else:
        json_count = _copy_report_jsons(reports_dir, staging_dir)
        print_info(f"Collected {json_count} report JSON(s) from {reports_dir.name}")

    # The archive is per-kernel data, so it lives inside the kernel's reports
    # directory; only the no-reports-dir fallback writes to the output root.
    rep_dir = reports_dir if reports_dir is not None else Path(output_abs)
    rep_file = rep_dir / f"{db_path.stem}.npu-rep"
    if rep_file.is_symlink() or rep_file.exists():
        rep_file.unlink()
    encoded = encode_rep(collect_entries(staging_dir))
    with open(rep_file, "xb") as out:
        out.write(encoded)
    print_info(f"Packed {rep_file.name} ({len(encoded)} bytes)")


def export_npu_rep(output_abs, input_db_path) -> None:
    """Run stage 3 for every contract DB under the report output directory.

    Best-effort: failures on one DB warn and continue; the staging directory
    is always removed at the end.
    """
    output_abs = Path(output_abs)
    input_db_path = Path(input_db_path)
    db_paths = _expand_db_list(input_db_path)
    if not db_paths:
        print_warn(f"No contract DB found for npu-rep export under {input_db_path}")
        return

    staging_dir = output_abs / _STAGING_DIR_NAME
    packed = 0
    try:
        for index, db_path in enumerate(db_paths):
            try:
                _pack_one_db(db_path, index, output_abs, staging_dir)
                packed += 1
            except (RepError, OSError, sqlite3.Error) as exc:
                print_warn(f"npu-rep export failed for {db_path.name}: {exc}")
    finally:
        if staging_dir.exists() or staging_dir.is_symlink():
            shutil.rmtree(staging_dir, ignore_errors=True)

    if packed != len(db_paths):
        print_warn(f"npu-rep export completed for {packed}/{len(db_paths)} DB file(s)")
    else:
        print_info(f"npu-rep export completed for {packed} DB file(s)")
