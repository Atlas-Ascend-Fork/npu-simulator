#!/bin/bash
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
#
# Python 3.7 compatibility gate.
#
# setup.py declares python_requires=">=3.7" and pyproject.toml sets
# target-version = "py37", but nothing in CI verified that claim: lint only
# runs ruff's F/E9 rules (no version awareness) and python_test runs on the
# runner's own interpreter (3.12+), where 3.8/3.9-only APIs work fine.
#
# Three layers are needed because no single tool covers everything:
#
#   1. Syntax     - ast.parse(feature_version) rejects walrus / match /
#                   positional-only params that 3.7 cannot parse at all.
#   2. Stdlib API - vermin knows which stdlib members were added when
#                   (e.g. typing.TypedDict 3.8, shlex.join 3.8).
#   3. Annotations- builtin generics (list[int], tuple[Path, str]) are
#                   evaluated at import time before 3.9 UNLESS the module has
#                   `from __future__ import annotations`. vermin cannot express
#                   this per-file distinction: --eval-annotations false-positives
#                   on modules that DO have the future import, while
#                   --no-eval-annotations assumes every module has it and so
#                   misses the ones that do not. Layer 3 closes that gap.
#                   Layer 3 also flags PEP 604 `X | Y` unions (3.10) in
#                   annotation positions when the future import is absent,
#                   since `|` parses as a plain expression on every version.
#
# vermin also misses str methods such as removesuffix/removeprefix (3.9),
# so layer 3 checks those too.
#
# Usage:  bash .gitcode/scripts/py37_compat.sh [target]     (default 3.7)

set -uo pipefail

TARGET="${1:-3.7}"
WORKSPACE="${WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "${WORKSPACE}"

# Scan the whole repo root so setup.py, tests/, and any future top-level
# package are covered — not just cannsim/. Vendored/generated/venv trees are
# excluded both in Layer 1/3 (EXCLUDE_PARTS) and Layer 2 (vermin regex), see
# below. Both layers must agree on the same exclusion set.
SCAN_DIRS="."
# Path components whose presence in a file's relative path means "skip".
# Shared conceptually with the vermin --exclude-regex list below; keep them
# in sync when editing.
EXCLUDE_PARTS="third_party .gitcode build dist venv .venv env __pycache__ .git"
RC=0

log() {
    echo "===================================================================="
    echo "$*"
    echo "===================================================================="
}

PIP_FLAGS=""
if pip3 install --help 2>&1 | grep -q -- '--break-system-packages'; then
    PIP_FLAGS="--break-system-packages"
fi

# ==============================
# Layer 1 + 3: syntax grammar and annotation evaluation
# ==============================
log "Layer 1/3: syntax (py${TARGET} grammar) and annotation checks"

python3 - "${TARGET}" ${SCAN_DIRS} -- ${EXCLUDE_PARTS} <<'PYEOF'
import ast
import pathlib
import sys

target = tuple(int(x) for x in sys.argv[1].split("."))
# argv: [0]=script [1]=target [2:]=scan_dirs then "--" then exclude_parts
try:
    sep = sys.argv.index("--")
except ValueError:
    scan_dirs = sys.argv[2:]
    exclude_parts = set()
else:
    scan_dirs = sys.argv[2:sep]
    exclude_parts = set(sys.argv[sep + 1:])

# Any path component in this set, or any component ending in '.egg-info',
# means the file lives in a vendored / generated / venv tree we must not
# scan. Passed in from EXCLUDE_PARTS so the shell list stays the single
# source of truth (vermin uses the same set via --exclude-regex).
exclude_parts.add("third_party")  # always excluded even if list is empty


def _excluded(p):
    parts = p.parts
    if any(part in exclude_parts for part in parts):
        return True
    if any(part.endswith(".egg-info") for part in parts):
        return True
    return False


# str methods added after 3.7 -> (major, minor) introduced
STR_METHODS = {"removeprefix": (3, 9), "removesuffix": (3, 9)}
BUILTIN_GENERICS = {"list", "dict", "set", "frozenset", "tuple", "type"}


def _iter_annotations(tree):
    """Yield every annotation expression node in the module.

    Covers function/method parameters (posonly/args/kwonly/vararg/kwarg),
    return annotations and variable annotations (AnnAssign). ``ast.walk``
    alone is not enough for PEP 604 detection because a ``BinOp(BitOr)`` in
    runtime code (e.g. ``mask |= 1``) must not be treated as a type union.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            params = (
                list(args.posonlyargs)
                + list(args.args)
                + list(args.kwonlyargs)
            )
            for param in params:
                if param.annotation is not None:
                    yield param.annotation
            if args.vararg is not None and args.vararg.annotation is not None:
                yield args.vararg.annotation
            if args.kwarg is not None and args.kwarg.annotation is not None:
                yield args.kwarg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            if node.annotation is not None:
                yield node.annotation


files = []
for d in scan_dirs:
    files.extend(
        f for f in sorted(pathlib.Path(d).rglob("*.py"))
        if not _excluded(f)
    )

violations = []
for f in files:
    src = f.read_text(encoding="utf-8")

    # --- Layer 1: can the target grammar even parse this file?
    try:
        ast.parse(src, filename=str(f), feature_version=target)
    except SyntaxError as e:
        violations.append((f, e.lineno, f"syntax not valid on py{sys.argv[1]}: {e.msg}"))
        continue

    tree = ast.parse(src, filename=str(f))
    postponed = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in ast.walk(tree)
    )

    for n in ast.walk(tree):
        # --- str.removeprefix / removesuffix (3.9), missed by vermin
        if isinstance(n, ast.Attribute) and n.attr in STR_METHODS:
            need = STR_METHODS[n.attr]
            if need > target:
                violations.append(
                    (f, n.lineno, f"str.{n.attr}() requires py{need[0]}.{need[1]}")
                )
        # --- Layer 3: builtin generic annotations evaluated at import time
        if not postponed and target < (3, 9):
            if isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name) \
                    and n.value.id in BUILTIN_GENERICS:
                violations.append((
                    f, n.lineno,
                    f"{n.value.id}[...] annotation requires py3.9 "
                    "(add 'from __future__ import annotations')",
                ))

    # --- Layer 3 (extended): PEP 604 `X | Y` unions in annotations (3.10) ---
    # ast.parse(feature_version) accepts `int | str` because `|` is valid
    # expression syntax everywhere, and vermin's --no-eval-annotations skips
    # annotations. So a bare `Path | None` annotation in a module lacking
    # `from __future__ import annotations` would be evaluated at import time
    # and raise TypeError on py<3.10 unless caught here.
    if not postponed and target < (3, 10):
        for ann in _iter_annotations(tree):
            for sub in ast.walk(ann):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    violations.append((
                        f, ann.lineno,
                        "`X | Y` union annotation requires py3.10 "
                        "(add 'from __future__ import annotations' "
                        "or use typing.Optional/Union)",
                    ))
                    break

print(f"scanned {len(files)} files in: {', '.join(scan_dirs)}")
if not violations:
    print(f"PASS: no syntax or annotation violations for py{sys.argv[1]}")
    sys.exit(0)

print(f"FAIL: {len(violations)} violation(s)")
for f, line, msg in violations:
    print(f"  {f}:{line}  {msg}")
sys.exit(1)
PYEOF

if [ $? -ne 0 ]; then
    RC=1
fi

# ==============================
# Layer 2: stdlib API surface (vermin)
# ==============================
log "Layer 2/3: stdlib API surface (vermin, target ${TARGET}-)"

# vermin ships a console script; it is not runnable via `python3 -m vermin`.
# The scripts dir may be absent from PATH on some images (same issue as
# pre_smoke.sh), so resolve it explicitly.
_PY_SCRIPTS=$(python3 -c "import sysconfig; print(sysconfig.get_path('scripts'))")
case ":${PATH}:" in
    *:"${_PY_SCRIPTS}":*) ;;
    *) export PATH="${_PY_SCRIPTS}:${PATH}" ;;
esac

if ! command -v vermin >/dev/null 2>&1; then
    echo "Installing vermin..."
    pip3 install ${PIP_FLAGS} "vermin==1.8.0" || {
        echo "ERROR: failed to install vermin"
        exit 1
    }
fi

if ! command -v vermin >/dev/null 2>&1; then
    echo "ERROR: vermin not on PATH after install (looked in ${_PY_SCRIPTS})"
    exit 1
fi

# --no-eval-annotations: annotations are handled by layer 3, which knows about
# per-file `from __future__ import annotations`. Without this flag vermin
# reports every builtin generic even in modules that correctly postpone them.
# vermin's --exclude-regex matches against the path as passed on the command
# line; with the default (make-paths-absolute) mode that is an absolute path,
# so the patterns below are anchored with (^|/)...(/|$) to match a path
# component anywhere in the absolute path. We deliberately do NOT pass
# --no-make-paths-absolute: this vermin version refuses to expand a bare "."
# scan dir when paths are relative ("No files specified to analyze!").
# Build one --exclude-regex per EXCLUDE_PARTS entry plus a '*.egg-info' suffix
# pattern, mirroring Layer 1's _excluded so the two layers stay in sync.
EXCLUDE_REGEX=()
for part in ${EXCLUDE_PARTS}; do
    EXCLUDE_REGEX+=(--exclude-regex "(^|/)${part}(/|$)")
done
EXCLUDE_REGEX+=(--exclude-regex "(^|/)[^/]+\.egg-info(/|$)")

vermin \
    --target="${TARGET}-" \
    --violations \
    --no-eval-annotations \
    --no-tips \
    "${EXCLUDE_REGEX[@]}" \
    ${SCAN_DIRS}
VERMIN_RC=$?

if [ "${VERMIN_RC}" -ne 0 ]; then
    RC=1
fi

# ==============================
# Summary
# ==============================
if [ "${RC}" -ne 0 ]; then
    log "PY${TARGET} COMPATIBILITY CHECK FAILED"
    echo "setup.py declares python_requires=\">=${TARGET}\"; the code above violates it."
    exit 1
fi

log "PY${TARGET} COMPATIBILITY CHECK PASSED"
exit 0
