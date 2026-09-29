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

from pathlib import Path

import pytest

import pip_audit_wrapper


@pytest.mark.parametrize("return_codes", [(0, 0), (1, 0), (0, 1)])
def test_relocated_audit_resolves_project_dependencies(monkeypatch, return_codes):
    root = Path(__file__).resolve().parent.parent
    assert pip_audit_wrapper.REPO_ROOT == root
    commands = []

    def audit(command):
        if not commands:
            project = Path(command[-1]) / "pyproject.toml"
            assert project.read_text(encoding="utf-8") == pip_audit_wrapper.RUNTIME_AUDIT_PROJECT
        else:
            index = command.index("--requirement")
            target = Path(command[index + 1])
            assert target == root / "tests" / "requirements.txt"
            assert target.is_file()
        commands.append(command)
        return return_codes[len(commands) - 1]

    monkeypatch.setattr(pip_audit_wrapper.subprocess, "call", audit)
    assert pip_audit_wrapper.main() == int(any(return_codes))
    assert len(commands) == 2
