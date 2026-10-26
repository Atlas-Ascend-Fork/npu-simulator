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

"""Human-readable rendering of the synthetic counters.

Pure module: takes values (and optionally a metric list) and returns
strings. Debug/tracing only - nothing here feeds a computation.
"""

from typing import Dict, List, Optional, Tuple

from npusim.prof.src.backend.analyzers.vf_pmu.metrics import (
    COUNTER_METRICS,
    FORMULAS,
    TOPDOWN_METRICS,
    Metric,
    RawUsage,
)


def render_raw_values(raw_used: Dict[str, RawUsage]) -> str:
    """Flat dump of the raw PMU counters the formulas actually read.

    Counters absent from the DB are flagged: they silently default to 0.0 and
    are a common reason a downstream metric collapses to zero.
    """
    if not raw_used:
        return "  (no raw counters)"
    width = max(len(name) for name in raw_used)
    lines = []
    for name, usage in raw_used.items():  # already sorted by evaluate()
        flag = "" if usage.present else "   <- MISSING in DB, defaulted to 0"
        lines.append(f"  {name.ljust(width)} = {usage.value:14.4f}{flag}")
    return "\n".join(lines)


def render_counter_values(values: Dict[str, float]) -> str:
    """Flat dump of the level 0 counters (value + formula), so every name a
    top-down formula references resolves to a number and to its origin.
    """
    present = [m for m in COUNTER_METRICS if m.name in values]
    if not present:
        return "  (no counters)"
    width = max(len(m.name) for m in present)
    return "\n".join(f"  {m.name.ljust(width)} = {values[m.name]:14.4f}   := {m.expr}" for m in present)


def _build_forest(metrics: List[Metric]) -> Tuple[List[str], Dict[str, List[str]]]:
    """Split the top-down metrics into roots + ordered children lists.

    A node whose parent is not in `metrics` becomes a root: that happens when
    the caller passes a filtered subset (for example the per-VF metrics).
    """
    names = {m.name for m in metrics}
    children: Dict[str, List[str]] = {m.name: [] for m in metrics}
    roots: List[str] = []
    for m in metrics:
        if m.parent is None or m.parent not in names:
            roots.append(m.name)
        else:
            children[m.parent].append(m.name)
    return roots, children


def render_topdown_tree(
    values: Dict[str, float],
    metrics: Optional[List[Metric]] = None,
) -> str:
    """ASCII tree of the level >= 1 metrics: each node shows its value, its
    share of its parent, and the formula (co-located with the computation, so
    it always matches).
    """
    metrics = TOPDOWN_METRICS if metrics is None else metrics
    if not any(m.name in values for m in metrics):
        return "  (top-down undefined: syn_c_optimal <= 0)"

    roots, children = _build_forest(metrics)
    lines: List[str] = []

    def describe(name: str, parent: Optional[str]) -> str:
        val = values.get(name)
        val_s = "     -    " if val is None else f"{val:10.4f}"
        share = ""
        if parent is not None:
            pv = values.get(parent)
            if val is not None and pv is not None and pv != 0:
                share = f"  [{val / pv * 100:6.1f}% of {parent}]"
        formula = FORMULAS.get(name)
        formula_s = f"   := {formula}" if formula else ""
        return f"{name} = {val_s}{share}{formula_s}"

    def walk(name: str, prefix: str, is_last: bool, parent: str) -> None:
        connector = "└── " if is_last else "├── "
        lines.append(prefix + connector + describe(name, parent))
        child_prefix = prefix + ("    " if is_last else "│   ")
        kids = children[name]
        for i, kid in enumerate(kids):
            walk(kid, child_prefix, i == len(kids) - 1, name)

    for root in roots:
        lines.append(describe(root, None))
        kids = children[root]
        for i, kid in enumerate(kids):
            walk(kid, "", i == len(kids) - 1, root)
    return "\n".join(lines)


def nodes_to_metrics(nodes: List[dict]) -> List[Metric]:
    """Turn rows fetched from the DB into Metric objects for rendering.

    Only the structural fields matter here; `fn` is never called. `expr` is
    looked up by name, so a node persisted by an older schema version simply
    renders without a formula rather than raising.
    """
    return [
        Metric(
            name=node["name"],
            expr=FORMULAS.get(node["name"], ""),
            fn=lambda c: 0.0,  # never evaluated; structure only
            level=node["level"],
            parent=node["parent_name"],
        )
        for node in nodes
    ]
