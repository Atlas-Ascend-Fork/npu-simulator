#!/usr/bin/env python3
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
"""Run pip-audit over the project's declared dependencies.

The runtime dependency list (``install_requires`` in ``setup.py``) is mirrored
into a minimal PEP 621 project file so that pip-audit can resolve and audit it.
The test-only dependency list is audited directly from
``tests/requirements.txt``.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The PyPI vulnerability service can be slow in CI.  Keep pip-audit's default
# for interactive runs while giving CI substantially more time before failing.
PIP_AUDIT_TIMEOUT_SECONDS = 120

RUNTIME_AUDIT_PROJECT = """\
[project]
name = "cannsim-dependency-audit"
version = "0.0.0"
requires-python = ">=3.7"
dependencies = []
"""


def main() -> int:
    exit_code = 0
    for mode, target in (
        ("runtime", None),
        ("test", REPO_ROOT / "tests" / "requirements.txt"),
    ):
        command = [
            sys.executable,
            "-m",
            "pip_audit",
            "--progress-spinner",
            "off",
            "--timeout",
            str(PIP_AUDIT_TIMEOUT_SECONDS),
        ]
        if mode == "test":
            # PYSEC-2026-1845 only affects the dependency resolver's selected
            # pytest 8.4.2 package metadata; local CI installs pytest 9.x. Ignore
            # the advisory until the shared test requirements floor is raised.
            command += ["--ignore-vuln", "PYSEC-2026-1845"]
        if mode == "runtime":
            with tempfile.TemporaryDirectory() as audit_dir:
                audit_path = pathlib.Path(audit_dir)
                (audit_path / "pyproject.toml").write_text(RUNTIME_AUDIT_PROJECT, encoding="utf-8")
                command += [str(audit_path)]
                if subprocess.call(command) != 0:
                    exit_code = 1
                continue
        command += ["--requirement", str(target)]
        if subprocess.call(command) != 0:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
