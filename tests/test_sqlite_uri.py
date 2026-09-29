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

import sqlite3
from unittest.mock import patch

import pytest

from npusim.prof_demo.database import DatabaseHandler
from test_core.test_prof_demo import make_db as make_report_db
from tools import verify_parallel_analysis


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
