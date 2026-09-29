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
# Build cannsim as a wheel package.
# Usage: bash build_wheel.sh [--clean]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CLEAN=0
if [[ "${1:-}" == "--clean" ]]; then
    CLEAN=1
fi

if [ "$CLEAN" -eq 1 ]; then
    echo "Cleaning previous build artifacts..."
    rm -rf build/ dist/ cannsim.egg-info
fi

echo "Building wheel..."
if ! python3 -c "import build" 2>/dev/null; then
    echo "Installing build tool..."
    pip3 install --user build
fi
python3 -m build --wheel

echo ""
echo "Wheel packages:"
ls -lh dist/*.whl 2>/dev/null || echo "No wheel found in dist/"
