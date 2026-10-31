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

import importlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from npusim.prof_demo.database import DatabaseHandler
from npusim.version import VERSION
from test_core.test_prof_demo import make_db as make_report_db
from tools import verify_parallel_analysis

# `npusim.core.report.report` 是模块，但包属性 `npusim.core.report.report` 已被
# 同名函数覆盖，故按 test_report.py 的做法用 importlib 取模块本体。
report_mod = importlib.import_module("npusim.core.report.report")
instr_log_parser = importlib.import_module("npusim.core.report.detections.instr_log_parser")


def test_uri_enabled_connection_and_repeated_sessions(tmp_path):
    source = tmp_path / "input #?%.db"
    make_report_db(source)
    original = source.read_bytes()
    handler = DatabaseHandler()
    try:
        with patch("npusim.prof_demo.database.sqlite3.connect", wraps=sqlite3.connect) as connect:
            for _ in range(2):
                handler.connect(source)
                connect.assert_called_with(":memory:", uri=True)
                assert handler.get_arch() == "v100"
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    handler.execute("UPDATE sim.KernelInfo SET KernelInfoVal=0")
                handler.disconnect()
                assert handler.connection is None
            assert connect.call_count == 2
    finally:
        handler.disconnect()
    assert source.read_bytes() == original


def make_db(path, value):
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE metrics (value INTEGER)")
        connection.execute("INSERT INTO metrics VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("name", ["record.db", "record #?%.db"])
@pytest.mark.parametrize("analysis_value, differences", [(1, 0), (2, 2)])
def test_comparison_enables_uri_and_attaches_read_only(tmp_path, monkeypatch, name, analysis_value, differences):
    record = tmp_path / name
    analysis = tmp_path / "analysis.db"
    make_db(record, 1)
    make_db(analysis, analysis_value)
    original = record.read_bytes()
    read_only_checks = []
    connections = []
    connect = sqlite3.connect

    class CheckedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith("ATTACH DATABASE"):
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    super().execute("UPDATE sim.metrics SET value=0")
                read_only_checks.append(True)
            return cursor

    def explicit_uri_connect(path, **kwargs):
        # Assert the explicit flag even on SQLite builds whose default is URI-enabled.
        assert kwargs.get("uri") is True
        connection = connect(path, factory=CheckedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(verify_parallel_analysis.sqlite3, "connect", explicit_uri_connect)
    assert verify_parallel_analysis.compare_tables(record, analysis, ["metrics"]) == differences
    assert read_only_checks == [True]
    assert record.read_bytes() == original
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_missing_read_only_database_is_not_created(tmp_path):
    record = tmp_path / "missing #?%.db"
    analysis = tmp_path / "analysis.db"
    make_db(analysis, 1)
    with pytest.raises(sqlite3.OperationalError):
        verify_parallel_analysis.compare_tables(record, analysis, ["metrics"])
    assert not record.exists()


def write_helper_db(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            "CREATE TABLE KernelInfo(KernelInfoAttr VARCHAR(60) NOT NULL, KernelInfoVal VARCHAR(60) NOT NULL)"
        )
        connection.execute("INSERT INTO KernelInfo VALUES ('helper_version', ?)", (VERSION,))
        connection.execute("INSERT INTO KernelInfo VALUES ('contract_version', '7')")
        connection.commit()
    finally:
        connection.close()
    return db_path


@pytest.mark.parametrize("dirname", ["export", "pr#42"])
def test_report_version_gates_read_db_under_uri_significant_dir(tmp_path, dirname):
    """上报前的 helper_version/contract_version 校验必须能读到 URI 特殊字符目录下的 DB。

    `pr#42` 里的 `#` 会被当作 URI fragment：用未编码的 `file:{path}` 拼 URI 时，
    SQLite 截断到 '#' 之前，取到错误的文件并让两个门禁误判为"DB 读不出来"。
    """
    db_path = write_helper_db(tmp_path / dirname / "record" / "0000_kernel.db")
    assert report_mod._check_helper_version(db_path) is True
    assert report_mod._check_contract_version(db_path) is True


def test_instr_db_loader_degrades_on_relative_path(tmp_path, monkeypatch):
    """相对路径的 record DB 必须走降级分支返回空结果，而不是抛 ValueError。

    `Path.as_uri()` 只接受绝对路径，未 resolve 就拼 URI 会抛
    ValueError: relative path can't be expressed as a file URI，
    该异常不在 `except sqlite3.Error` 覆盖范围内，会穿透报告中止出图。
    """
    write_helper_db(tmp_path / "record" / "0000_kernel.db")
    monkeypatch.chdir(tmp_path)
    assert instr_log_parser._load_device_pcs_from_record_db(Path("record") / "0000_kernel.db") == []
