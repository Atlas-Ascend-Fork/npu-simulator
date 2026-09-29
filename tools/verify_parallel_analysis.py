#!/usr/bin/env python3
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

"""Compare npusim-derived analysis tables with helper parallel outputs."""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


PARALLEL_TABLES = (
    "UnitsUsageMetrics",
    "RooflineReference",
    "DmaMovProcessedBytes",
    "PipesUtilization",
    "ArchDiagramMetrics",
    "ArchDiagramBandwidthUtilization",
    "VfIPC",
    "VfIPCInside",
    "VfSimtIPC",
    "PipeDependency",
)

# E7: helper QueryDouble reads SQLite aggregates through sqlite3_exec's text
# callback, reducing REAL values to 15 significant digits before strtod.  Only
# these two helper tables contain values from that path.  Remove this exception
# with the helper-side parallel outputs in 0.5.4.
TEXT_ROUNDTRIP_TABLES = {
    "ArchDiagramMetrics",
    "ArchDiagramBandwidthUtilization",
}


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def find_analysis_db(record_db: Path) -> Path:
    if not record_db.is_file():
        raise ValueError(f"record DB does not exist: {record_db}")
    if record_db.parent.name != "record":
        raise ValueError(f"record DB must be directly under a record/ directory: {record_db}")
    launch_prefix = record_db.name.split("_", 1)[0] + "_"
    results_dir = record_db.parent.parent / "report" / "results"
    candidates = sorted(results_dir.glob(f"{launch_prefix}*_reports/analysis.db"))
    if len(candidates) != 1:
        rendered = ", ".join(str(path) for path in candidates) or "none"
        raise ValueError(f"expected one analysis DB for {record_db.name}, found {len(candidates)}: {rendered}")
    return candidates[0]


def _validate_table_name(table: str) -> str:
    if not table.replace("_", "").isalnum():
        raise ValueError(f"invalid table name: {table}")
    return table


def _table_columns(connection: sqlite3.Connection, schema: str, table: str) -> List[Tuple[str, str]]:
    rows = connection.execute(f'PRAGMA {schema}.table_info("{table}")').fetchall()
    return [(str(row[1]), str(row[2]).upper()) for row in rows]


def _is_real_column(declared_type: str) -> bool:
    return any(token in declared_type for token in ("REAL", "FLOAT", "DOUBLE"))


def _rows_by_key(rows: Iterable[Tuple], key_indices: Sequence[int]) -> Optional[Dict[Tuple, Tuple]]:
    result: Dict[Tuple, Tuple] = {}
    for row in rows:
        values = tuple(row)
        key = tuple(values[index] for index in key_indices)
        if key in result:
            return None
        result[key] = values
    return result


def _check_text_roundtrip(connection: sqlite3.Connection, table: str) -> Optional[List[str]]:
    """Return E7 cells, or None if any difference is not an E7 equality."""
    if table not in TEXT_ROUNDTRIP_TABLES:
        return None
    main_columns = _table_columns(connection, "main", table)
    sim_columns = _table_columns(connection, "sim", table)
    if not main_columns or main_columns != sim_columns:
        return None

    real_indices = [index for index, (_, declared_type) in enumerate(main_columns) if _is_real_column(declared_type)]
    key_indices = [index for index in range(len(main_columns)) if index not in real_indices]
    if not real_indices or not key_indices:
        return None

    main_rows = _rows_by_key(connection.execute(f'SELECT * FROM main."{table}"'), key_indices)
    sim_rows = _rows_by_key(connection.execute(f'SELECT * FROM sim."{table}"'), key_indices)
    if main_rows is None or sim_rows is None or main_rows.keys() != sim_rows.keys():
        return None

    details: List[str] = []
    for key, computed_row in main_rows.items():
        stored_row = sim_rows[key]
        key_text = ",".join(f"{main_columns[index][0]}={key[position]}" for position, index in enumerate(key_indices))
        for index in real_indices:
            computed_val = computed_row[index]
            stored_val = stored_row[index]
            if stored_val == computed_val:
                continue
            if not isinstance(computed_val, (int, float)) or not isinstance(stored_val, (int, float)):
                return None
            # This is an exact serialization-equivalence check, not a numeric
            # tolerance: only helper's 15-significant-digit text roundtrip passes.
            if stored_val != float("%.15g" % computed_val):
                return None
            details.append(f"{main_columns[index][0]}[{key_text}]=helper {stored_val!r} vs npusim {computed_val!r}")
    return details


def compare_tables(record_db: Path, analysis_db: Path, tables: Iterable[str]) -> int:
    connection = sqlite3.connect(analysis_db, uri=True)
    try:
        connection.execute("ATTACH DATABASE ? AS sim", (_read_only_uri(record_db),))
        differences = 0
        for raw_table in tables:
            table = _validate_table_name(raw_table)
            directions = (
                ("main-minus-sim", "main", "sim"),
                ("sim-minus-main", "sim", "main"),
            )
            counts = []
            for label, left, right in directions:
                query = f'SELECT COUNT(*) FROM (SELECT * FROM {left}."{table}" EXCEPT SELECT * FROM {right}."{table}")'
                count = int(connection.execute(query).fetchone()[0])
                counts.append((label, count))
            main_rows = int(connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0])
            sim_rows = int(connection.execute(f'SELECT COUNT(*) FROM sim."{table}"').fetchone()[0])
            strict_match = all(count == 0 for _, count in counts)
            roundtrip_details: Optional[List[str]] = None
            if not strict_match:
                roundtrip_details = _check_text_roundtrip(connection, table)
            accepted_roundtrip = bool(roundtrip_details)
            if not strict_match and not accepted_roundtrip:
                differences += sum(count for _, count in counts)
            detail = " ".join(f"{label}={count}" for label, count in counts)
            if accepted_roundtrip:
                detail += f" text-roundtrip-diff-count={len(roundtrip_details)} ({', '.join(roundtrip_details)})"
            status = "PASS" if strict_match or accepted_roundtrip else "FAIL"
            print(f"{status} {table}: main_rows={main_rows} sim_rows={sim_rows} {detail}")
        return differences
    finally:
        connection.close()


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare analysis.db tables with helper parallel outputs.")
    parser.add_argument("record_db", type=Path, help="helper-generated record DB")
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        help="table to compare; repeatable (defaults to the current batch)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] = ()) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        analysis_db = find_analysis_db(args.record_db)
        print(f"record={args.record_db.resolve()}")
        print(f"analysis={analysis_db.resolve()}")
        differences = compare_tables(args.record_db, analysis_db, args.tables or PARALLEL_TABLES)
    except (ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if differences:
        print(f"parallel analysis verification failed: {differences} difference(s)")
        return 1
    print("parallel analysis verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
