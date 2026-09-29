#!/usr/bin/env bash
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

#
# Clean build, cache, test, coverage, audit, and temporary artifacts for Python project.
# Usage: bash clear.sh [--dry-run|-n] [--verbose|-v] [--help|-h]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DRY_RUN=0
VERBOSE=0

show_help() {
    cat <<'EOF'
Usage: bash clear.sh [OPTIONS]

Clean build, cache, test, coverage, audit, and temporary artifacts in the repository.

Options:
  -n, --dry-run    List artifacts that would be removed without deleting them
  -v, --verbose    Show each file and directory being removed
  -h, --help       Show this help message
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        -v|--verbose)
            VERBOSE=1
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            show_help >&2
            exit 1
            ;;
    esac
done

if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry-run mode: scanning for artifacts to clean in $SCRIPT_DIR..."
else
    echo "Cleaning build, cache, test, and temporary artifacts..."
fi

# 1. Clean root-level build, test, coverage, and audit directories
ROOT_DIRS=(
    build
    dist
    .eggs
    cannsim.egg-info
    .pytest_cache
    .ruff_cache
    .mypy_cache
    .tox
    .nox
    .cache
    htmlcov
    oat_reports
    smoke_logs
    leakage_audit
    .omo
    test
)

for dir in "${ROOT_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        if [ "$DRY_RUN" -eq 1 ]; then
            echo "[dry-run] dir:  $dir/"
        else
            [ "$VERBOSE" -eq 1 ] && echo "Removing dir: $dir/"
            rm -rf "$dir"
        fi
    fi
done

# Clean root-level temporary files and packages
shopt -s nullglob
ROOT_FILES=(
    .coverage
    .coverage.*
    coverage.xml
    coverage.json
    *.whl
    *.egg-info
    NUL
)
for pattern in "${ROOT_FILES[@]}"; do
    for f in $pattern; do
        if [ -e "$f" ] || [ -L "$f" ]; then
            if [ "$DRY_RUN" -eq 1 ]; then
                echo "[dry-run] file: $f"
            else
                [ "$VERBOSE" -eq 1 ] && echo "Removing file: $f"
                rm -rf "$f"
            fi
        fi
    done
done
shopt -u nullglob

# 2. Recursively clean Python cache and temporary directories throughout the repository
# Prunes .git so repository history is untouched.
if [ "$DRY_RUN" -eq 1 ]; then
    find . -name .git -prune -o \
        -type d \( -name '__pycache__' -o -name '*.egg-info' -o -name '.pytest_cache' -o \
                   -name '.ruff_cache' -o -name '.mypy_cache' -o -name 'htmlcov' \) \
        -prune -exec echo "[dry-run] dir:  {}/" \;
else
    if [ "$VERBOSE" -eq 1 ]; then
        find . -name .git -prune -o \
            -type d \( -name '__pycache__' -o -name '*.egg-info' -o -name '.pytest_cache' -o \
                       -name '.ruff_cache' -o -name '.mypy_cache' -o -name 'htmlcov' \) \
            -prune -print -exec rm -rf {} + 2>/dev/null || true
    else
        find . -name .git -prune -o \
            -type d \( -name '__pycache__' -o -name '*.egg-info' -o -name '.pytest_cache' -o \
                       -name '.ruff_cache' -o -name '.mypy_cache' -o -name 'htmlcov' \) \
            -prune -exec rm -rf {} + 2>/dev/null || true
    fi
fi

# 3. Recursively clean Python compiled bytecode, test coverage, logs, and temporary files
if [ "$DRY_RUN" -eq 1 ]; then
    find . -name .git -prune -o \
        -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pyd' -o \
                   -name '*.log' -o -name '.coverage*' -o \
                   -name 'coverage.xml' -o -name 'coverage.json' -o \
                   -name 'NUL' -o -name '*~' -o -name '*.swp' -o -name '*.swo' -o \
                   -name '.*.swp' -o -name '.DS_Store' -o -name 'Thumbs.db' -o \
                   -name '.fuse_hidden*' \) \
        -exec echo "[dry-run] file: {}" \;
else
    if [ "$VERBOSE" -eq 1 ]; then
        find . -name .git -prune -o \
            -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pyd' -o \
                       -name '*.log' -o -name '.coverage*' -o \
                       -name 'coverage.xml' -o -name 'coverage.json' -o \
                       -name 'NUL' -o -name '*~' -o -name '*.swp' -o -name '*.swo' -o \
                       -name '.*.swp' -o -name '.DS_Store' -o -name 'Thumbs.db' -o \
                       -name '.fuse_hidden*' \) \
            -print -exec rm -f {} + 2>/dev/null || true
    else
        find . -name .git -prune -o \
            -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.pyd' -o \
                       -name '*.log' -o -name '.coverage*' -o \
                       -name 'coverage.xml' -o -name 'coverage.json' -o \
                       -name 'NUL' -o -name '*~' -o -name '*.swp' -o -name '*.swo' -o \
                       -name '.*.swp' -o -name '.DS_Store' -o -name 'Thumbs.db' -o \
                       -name '.fuse_hidden*' \) \
            -exec rm -f {} + 2>/dev/null || true
    fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "Dry-run complete."
else
    echo "Done."
fi
