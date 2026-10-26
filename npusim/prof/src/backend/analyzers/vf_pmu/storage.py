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

"""Data access for the VF PMU counters, raw and synthetic.

Every SQL statement touching them lives here: metrics.py stays pure, the
analyzer stays orchestration-only, and the reporters stay presentation-only.

Two scopes, two value tables, one shared type table (VfPMUSynthTypes,
VfPMUSynthValuesPerSubcore, VfPMUSynthValuesPerVf). Reads come in two
shapes: fetch_all_* pulls every subcore in one query (used by the analyzer);
fetch_* pulls one subcore, optionally one subset of names (used by the
reporters).

Names are STORAGE names throughout: c_* for raw counters, syn_c_* for
synthetics. Stripping the prefix for display is the caller's job
(metrics.display_name).
"""

from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from npusim.prof.src.backend.analyzers.vf_pmu.metrics import (
    METRICS,
    MetricScope,
    scope_of,
)
from npusim.prof.src.backend.database import database_handler

# Keyed by (CoreId, CoreTypeId) and (CoreId, CoreTypeId, VfId).
SubcoreKey = Tuple[int, int]
VfKey = Tuple[int, int, int]

_INDEX_NAME = "idx_vfpmuvalues_core_vf"


def make_indexes() -> None:
    """VfPMUValues has no PK or index of its own, so VfPMUViewPerSubcore's
    correlated MAX(VfId) subquery is one scan per row (O(rows^2)) without
    this. VfPMUDeltasViewPerVf also benefits, via its (CoreId, CoreTypeId)
    window partition."""
    database_handler.execute(f"""
        CREATE INDEX IF NOT EXISTS {_INDEX_NAME}
        ON VfPMUValues (CoreId, CoreTypeId, VfId)
    """)
    database_handler.commit()


def drop_indexes() -> None:
    database_handler.execute(f"DROP INDEX IF EXISTS {_INDEX_NAME}")
    database_handler.commit()


def _name_filter(
    column: str,
    names: Optional[Sequence[str]],
    params: Dict[str, object],
) -> str:
    """Build an optional `AND <column> IN (...)`, filling `params` in place."""
    if names is None:
        return ""
    placeholders = ", ".join(f":name_{i}" for i in range(len(names)))
    params.update({f"name_{i}": name for i, name in enumerate(names)})
    return f"AND {column} IN ({placeholders})"


# ---------------------------------------------------------------------------
# Metric dictionary
# ---------------------------------------------------------------------------


def register_synth_types() -> Dict[str, int]:
    """Insert the metric dictionary and return {name: SynthId}.

    Ids follow METRICS order, so a parent always has a smaller id than its
    children and the self-referencing FK is satisfiable in a single pass.
    """
    name_to_id = {m.name: i + 1 for i, m in enumerate(METRICS)}
    database_handler.executemany(
        """
        INSERT OR IGNORE INTO VfPMUSynthTypes
        (SynthId, SynthName, SynthParentId, SynthLevel, SynthScope)
        VALUES(?, ?, ?, ?, ?)
    """,
        [
            (
                name_to_id[m.name],
                m.name,
                name_to_id[m.parent] if m.parent else None,
                m.level,
                scope_of(m).value,
            )
            for m in METRICS
        ],
    )
    database_handler.commit()
    rows = database_handler.execute("SELECT SynthId, SynthName FROM VfPMUSynthTypes").fetchall()
    return {row["SynthName"]: row["SynthId"] for row in rows}


# ---------------------------------------------------------------------------
# Bulk reads (analyzer)
# ---------------------------------------------------------------------------


def fetch_all_subcore_metrics() -> Dict[SubcoreKey, Dict[str, float]]:
    """Cumulative counters of the last VF, for every (core, coretype)."""
    rows = database_handler.execute("""
        SELECT CoreId, CoreTypeId, MetricName, MetricValue
        FROM VfPMUViewPerSubcore
    """).fetchall()
    result: Dict[SubcoreKey, Dict[str, float]] = {}
    for row in rows:
        key = (row["CoreId"], row["CoreTypeId"])
        result.setdefault(key, {})[row["MetricName"]] = float(row["MetricValue"])
    return result


def fetch_all_vf_deltas() -> Dict[VfKey, Dict[str, float]]:
    """Per-VF deltas of the cumulative counters, for every VF.

    One query rather than one per subcore: the analyzer needs all of them
    anyway, and one round trip is simpler.
    """
    rows = database_handler.execute("""
        SELECT CoreId, CoreTypeId, VfId, MetricName, MetricDelta
        FROM VfPMUDeltasViewPerVf
        ORDER BY CoreId, CoreTypeId, VfId
    """).fetchall()
    result: Dict[VfKey, Dict[str, float]] = {}
    for row in rows:
        key = (row["CoreId"], row["CoreTypeId"], row["VfId"])
        result.setdefault(key, {})[row["MetricName"]] = float(row["MetricDelta"])
    return result


# ---------------------------------------------------------------------------
# Scoped reads (reporters)
# ---------------------------------------------------------------------------


def fetch_subcore_metrics(
    core_id: int,
    core_type_id: int,
    names: Optional[Sequence[str]] = None,
) -> Dict[str, float]:
    """{metric_name: cumulative value of the last VF} for one subcore."""
    params: Dict[str, object] = {"core_id": core_id, "core_type_id": core_type_id}
    clause = _name_filter("MetricName", names, params)
    rows = database_handler.execute(
        f"""
        SELECT MetricName, MetricValue
        FROM VfPMUViewPerSubcore
        WHERE CoreId = :core_id AND CoreTypeId = :core_type_id
        {clause}
    """,
        params,
    ).fetchall()
    return {row["MetricName"]: float(row["MetricValue"]) for row in rows}


def fetch_vf_deltas(
    core_id: int,
    core_type_id: int,
    names: Optional[Sequence[str]] = None,
) -> Dict[int, Dict[str, float]]:
    """{vf_id: {metric_name: delta}} for one subcore.

    A VF with no rows for the requested names simply does not appear.
    """
    params: Dict[str, object] = {"core_id": core_id, "core_type_id": core_type_id}
    clause = _name_filter("MetricName", names, params)
    rows = database_handler.execute(
        f"""
        SELECT VfId, MetricName, MetricDelta
        FROM VfPMUDeltasViewPerVf
        WHERE CoreId = :core_id AND CoreTypeId = :core_type_id
        {clause}
        ORDER BY VfId
    """,
        params,
    ).fetchall()
    result: Dict[int, Dict[str, float]] = {}
    for row in rows:
        result.setdefault(row["VfId"], {})[row["MetricName"]] = float(row["MetricDelta"])
    return result


def fetch_vf_kernel_ticks(core_id: int, core_type_id: int) -> Dict[int, int]:
    """{vf_id: VfKernelTicks} for one subcore."""
    rows = database_handler.execute(
        """
        SELECT VfId, VfKernelTicks
        FROM VfPMUSummary
        WHERE CoreId = ? AND CoreTypeId = ?
        ORDER BY VfId
    """,
        (core_id, core_type_id),
    ).fetchall()
    return {row["VfId"]: row["VfKernelTicks"] for row in rows}


def fetch_subcore_synth_values(
    core_id: int,
    core_type_id: int,
    names: Optional[Sequence[str]] = None,
    min_level: int = 0,
) -> Dict[str, float]:
    """{syn_c_name: value} for one subcore.

    `min_level=1` drops the level 0 intermediate counters, leaving only the
    top-down hierarchy.
    """
    params: Dict[str, object] = {
        "core_id": core_id,
        "core_type_id": core_type_id,
        "min_level": min_level,
    }
    clause = _name_filter("SynthName", names, params)
    rows = database_handler.execute(
        f"""
        SELECT SynthName, SynthValue
        FROM VfPMUSynthViewPerSubcore
        WHERE CoreId = :core_id AND CoreTypeId = :core_type_id
            AND SynthLevel >= :min_level
        {clause}
    """,
        params,
    ).fetchall()
    return {row["SynthName"]: float(row["SynthValue"]) for row in rows}


def fetch_vf_synth_values(
    core_id: int,
    core_type_id: int,
    names: Optional[Sequence[str]] = None,
    min_level: int = 0,
) -> Dict[int, Dict[str, float]]:
    """{vf_id: {syn_c_name: value}} for one subcore.

    A VF whose syn_c_optimal was <= 0 has no hierarchy rows at all, so it is
    absent from the inner dicts rather than carrying misleading zeros.
    """
    params: Dict[str, object] = {
        "core_id": core_id,
        "core_type_id": core_type_id,
        "min_level": min_level,
    }
    clause = _name_filter("SynthName", names, params)
    rows = database_handler.execute(
        f"""
        SELECT VfId, SynthName, SynthValue
        FROM VfPMUSynthViewPerVf
        WHERE CoreId = :core_id AND CoreTypeId = :core_type_id
            AND SynthLevel >= :min_level
        {clause}
        ORDER BY VfId
    """,
        params,
    ).fetchall()
    result: Dict[int, Dict[str, float]] = {}
    for row in rows:
        result.setdefault(row["VfId"], {})[row["SynthName"]] = float(row["SynthValue"])
    return result


def _nodes_from_rows(rows: Iterable) -> List[dict]:
    return [
        {
            "id": row["SynthId"],
            "name": row["SynthName"],
            "parent_name": row["SynthParentName"],
            "level": row["SynthLevel"],
            "value": float(row["SynthValue"]),
        }
        for row in rows
    ]


def fetch_subcore_topdown_nodes(core_id: int, core_type_id: int) -> List[dict]:
    """Hierarchy nodes with values for one subcore.

    Ordered by SynthId, which follows METRICS order, so a parent always
    precedes its children and a tree builder can rely on that.
    """
    rows = database_handler.execute(
        """
        SELECT SynthId, SynthName, SynthParentName, SynthLevel, SynthValue
        FROM VfPMUSynthViewPerSubcore
        WHERE CoreId = ? AND CoreTypeId = ? AND SynthLevel >= 1
        ORDER BY SynthId
    """,
        (core_id, core_type_id),
    ).fetchall()
    return _nodes_from_rows(rows)


def fetch_vf_topdown_nodes(core_id: int, core_type_id: int, vf_id: int) -> List[dict]:
    """Hierarchy nodes with values for one VF, parents before children."""
    rows = database_handler.execute(
        """
        SELECT SynthId, SynthName, SynthParentName, SynthLevel, SynthValue
        FROM VfPMUSynthViewPerVf
        WHERE CoreId = ? AND CoreTypeId = ? AND VfId = ?
            AND SynthLevel >= 1
        ORDER BY SynthId
    """,
        (core_id, core_type_id, vf_id),
    ).fetchall()
    return _nodes_from_rows(rows)


# ---------------------------------------------------------------------------
# Writes (analyzer)
# ---------------------------------------------------------------------------


def save_subcore_synth_values(
    name_to_id: Dict[str, int],
    core_id: int,
    core_type_id: int,
    values: Dict[str, float],
) -> None:
    database_handler.executemany(
        """
        INSERT INTO VfPMUSynthValuesPerSubcore
        (CoreId, CoreTypeId, SynthId, SynthValue)
        VALUES(?, ?, ?, ?)
    """,
        [
            (core_id, core_type_id, name_to_id[name], float(value))
            for name, value in values.items()
            if name in name_to_id
        ],
    )


def save_vf_synth_values(
    name_to_id: Dict[str, int],
    rows: Iterable[Tuple[VfKey, Dict[str, float]]],
) -> None:
    """Bulk insert for every VF at once: one executemany, not one per VF."""
    payload = [
        (core_id, core_type_id, vf_id, name_to_id[name], float(value))
        for (core_id, core_type_id, vf_id), values in rows
        for name, value in values.items()
        if name in name_to_id
    ]
    if not payload:
        return
    database_handler.executemany(
        """
        INSERT INTO VfPMUSynthValuesPerVf
        (CoreId, CoreTypeId, VfId, SynthId, SynthValue)
        VALUES(?, ?, ?, ?, ?)
    """,
        payload,
    )


# Convenience for callers that only want the per-VF hierarchy names.
VF_SCOPE = MetricScope.VF
SUBCORE_SCOPE = MetricScope.SUBCORE
