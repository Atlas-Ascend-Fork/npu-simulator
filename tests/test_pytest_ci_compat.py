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

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import pytest_ci_compat


def test_legacy_cov_options_are_mapped_before_collection(tmp_path, monkeypatch):
    (tmp_path / "npusim").mkdir()
    monkeypatch.setattr(pytest_ci_compat, "__file__", str(tmp_path / ".gitcode" / "scripts" / "pytest_ci_compat.py"))
    options = SimpleNamespace(cov_source=["cannsim", "another_package"])
    plugin = mock.Mock(options=SimpleNamespace(cov_source=list(options.cov_source)))
    config = SimpleNamespace(option=options, pluginmanager=mock.Mock())
    config.pluginmanager.getplugin.return_value = plugin
    pytest_ci_compat.pytest_configure(config)
    assert options.cov_source == [str(tmp_path / "npusim"), "another_package"]
    assert plugin.options.cov_source == options.cov_source
    plugin.cov_controller.cov.stop.assert_called_once_with()
    plugin.start.assert_called_once_with(type(plugin.cov_controller))


@pytest.mark.parametrize("case", ["no_cov", "all_sources", "disabled", "modern", "existing_legacy", "missing_current"])
def test_unrelated_cov_options_are_unchanged(tmp_path, monkeypatch, case):
    monkeypatch.setattr(pytest_ci_compat, "__file__", str(tmp_path / ".gitcode" / "scripts" / "pytest_ci_compat.py"))
    if case != "missing_current":
        (tmp_path / "npusim").mkdir()
    if case == "existing_legacy":
        (tmp_path / "cannsim").mkdir()
    sources = [] if case == "no_cov" else ["npusim" if case == "modern" else "cannsim"]
    if case == "all_sources":
        sources.append(True)
    options = SimpleNamespace(cov_source=list(sources), no_cov=case == "disabled")
    manager = mock.Mock()
    pytest_ci_compat.pytest_configure(SimpleNamespace(option=options, pluginmanager=manager))
    assert options.cov_source == sources
    manager.getplugin.assert_not_called()


@pytest.mark.parametrize("cov_args", [["--cov=cannsim"], ["--cov=npusim"], ["--cov", "cannsim"]])
def test_console_pytest_records_real_source_coverage(tmp_path, cov_args):
    pytest.importorskip("pytest_cov")
    root = tmp_path / "project"
    root.mkdir()
    (root / ".gitcode" / "scripts").mkdir(parents=True)
    (root / "npusim").mkdir()
    (root / "tests").mkdir()
    source_root = Path(__file__).resolve().parent.parent
    shutil.copyfile(
        source_root / ".gitcode" / "scripts" / "pytest_ci_compat.py",
        root / ".gitcode" / "scripts" / "pytest_ci_compat.py",
    )
    shutil.copyfile(source_root / "pyproject.toml", root / "pyproject.toml")
    shutil.copyfile(source_root / "tests" / "conftest.py", root / "tests" / "conftest.py")
    (root / "npusim" / "__init__.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    (root / "tests" / "test_sample.py").write_text(
        "import npusim\n\ndef test_answer():\n    assert npusim.answer() == 42\n", encoding="utf-8"
    )
    # Run like the pytest console script: project root is not initially on sys.path.
    launcher = tmp_path / "pytest_launcher.py"
    launcher.write_text("import pytest\nraise SystemExit(pytest.console_main())\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTHONPATH", None)
    env.pop("COV_CORE_SOURCE", None)
    env.pop("COV_CORE_CONFIG", None)
    env.pop("COV_CORE_DATAFILE", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["COVERAGE_FILE"] = str(root / ".coverage")
    result = subprocess.run(
        [
            sys.executable,
            str(launcher),
            "-p",
            "pytest_cov.plugin",
            "-p",
            "no:cacheprovider",
            "tests/",
            "-q",
            *cov_args,
            "--cov-report=json:coverage.json",
            "--cov-report=html:htmlcov",
        ],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout
    assert "module-not-imported" not in result.stdout
    assert "no-data-collected" not in result.stdout
    report = json.loads((root / "coverage.json").read_text(encoding="utf-8"))
    assert set(report["files"]) == {"npusim/__init__.py"}
    assert report["files"]["npusim/__init__.py"]["executed_lines"] == [1, 2]
    assert (root / "htmlcov" / "index.html").is_file()
