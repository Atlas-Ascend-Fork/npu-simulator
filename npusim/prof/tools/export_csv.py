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

"""Export every table and view in a contract/analysis SQLite database to CSV.

For each table and view in the database a CSV file is written to the output
directory.  Empty tables/views are skipped (no CSV file is emitted) but
still appear in ``manifest.json`` with ``row_count`` 0 so the schema
remains visible to downstream consumers.  A ``manifest.json`` file records
metadata (name, type, row count, columns) for every exported object.

Cell value conventions:
  * ``NULL``   -> empty string
  * ``BLOB``   -> ``0x``-prefixed lower-case hex string
  * other types are rendered by :mod:`csv` as-is (ints, floats, text).

Library module used by the ``npusim report`` stage 3 (.npu-rep packing);
not runnable as a standalone tool.

Only the Python standard library is required.
"""

import csv
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _quote_ident(name):
    """Quote a SQL identifier with double quotes, escaping embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


def _cell_to_str(value):
    """Convert a single cell value to a CSV-friendly representation.

    ``None`` becomes an empty string; ``bytes``/``bytearray`` become a
    ``0x``-prefixed hex string.  Everything else is returned unchanged and
    left to :class:`csv.writer` to serialise.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return "0x" + bytes(value).hex()
    return value


def _sqlite_ln(x):
    """Natural logarithm, matching SQLite 3.35+'s built-in ``LN``.

    SQLite math functions (including ``LN``) were added in 3.35.0.  Older
    distributions (e.g. Ubuntu 20.04 ships 3.31.x) lack them, so we register
    a Python-side replacement to keep view definitions that use ``LN``
    parsable on every platform.
    """
    if x is None:
        return None
    try:
        val = float(x)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    return math.log(val)


def _register_compat_functions(conn):
    """Register Python functions for SQL built-ins missing on old SQLite."""
    conn.create_function("LN", 1, _sqlite_ln)


def export_one(conn, name, obj_type, out_dir):
    """Export a single table or view to ``<out_dir>/<name>.csv``.

    Returns a metadata dict for the manifest.  Empty tables/views are skipped
    (no CSV file is written) but still appear in the manifest with
    ``row_count`` 0.  When the object cannot be queried (for example a view
    whose underlying table is missing) the error is recorded and no file is
    written.
    """
    # Column metadata.  PRAGMA table_info is the primary path, but it can
    # fail on old SQLite versions when a view definition references a
    # function the engine does not recognise (e.g. LN on SQLite < 3.35).
    # Fall back to "SELECT * FROM <name> LIMIT 0" and cursor.description.
    columns = []
    col_names = []
    error = None
    try:
        pragma_rows = conn.execute("PRAGMA table_info(%s)" % _quote_ident(name)).fetchall()
        columns = [{"name": r[1], "type": (r[2] or "")} for r in pragma_rows]
        col_names = [c["name"] for c in columns]
    except sqlite3.Error:
        pass  # handled by the SELECT below or the fallback

    csv_path = out_dir / (name + ".csv")
    row_count = 0
    wrote_file = False
    try:
        cur = conn.execute("SELECT * FROM %s" % _quote_ident(name))
        if not col_names:
            col_names = [d[0] for d in cur.description]
            columns = [{"name": n, "type": ""} for n in col_names]
        first = cur.fetchone()
        if first is None:
            cur.close()
        else:
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(col_names)
                writer.writerow([_cell_to_str(v) for v in first])
                row_count = 1
                for row in cur:
                    writer.writerow([_cell_to_str(v) for v in row])
                    row_count += 1
            wrote_file = True
    except sqlite3.Error as exc:
        error = str(exc)
        if not col_names:
            col_names = []
            columns = []

    meta = {
        "name": name,
        "type": obj_type,
        "row_count": row_count,
        "columns": columns,
    }
    if wrote_file:
        meta["file"] = name + ".csv"
    if error:
        meta["error"] = error
    return meta


def export_database(db_path, out_dir):
    """Export every table and view in *db_path* into *out_dir*.

    Returns the manifest dict that was also written to ``manifest.json``.
    """
    db_path = Path(db_path).resolve()
    out_dir = Path(out_dir).resolve()
    if not db_path.is_file():
        raise FileNotFoundError("Database not found: %s" % db_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    _register_compat_functions(conn)
    try:
        objects = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        entries = []
        total_rows = 0
        for obj_type, name in objects:
            meta = export_one(conn, name, obj_type, out_dir)
            entries.append(meta)
            total_rows += meta["row_count"]
    finally:
        conn.close()

    manifest = {
        "database": str(db_path),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "total_objects": len(entries),
        "total_tables": sum(1 for e in entries if e["type"] == "table"),
        "total_views": sum(1 for e in entries if e["type"] == "view"),
        "total_rows": total_rows,
        "objects": entries,
    }
    with open(out_dir / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return manifest
