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

import sqlite3
import os
import math
from typing import Optional, Dict, Sequence, Union, Any, Iterator

from npusim.prof.src.common.biprof_enum import AnalysisType
from npusim.prof.src.common.utils import is_in_pyinstaller_env, get_pyinstaller_path
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.biprof_constants import CONTRACT_ARCH_ALIASES


class DatabaseHandler:
    DB_LOCK_TIMEOUT = 30

    __connection = None
    __cursor = None
    __db_path = None

    def _define_custom_functions(self):
        def ln(x):
            if x is None or x <= 0:
                return None
            return math.log(x)

        self.__connection.create_function("LN", 1, ln)

    def connect(self, database_path: str) -> None:
        if self.__connection is None:
            self.__db_path = database_path
            if biprof_ctx.memory_utilization:
                self.DB_LOCK_TIMEOUT *= 2
            self.__connection = sqlite3.connect(self.__db_path, timeout=self.DB_LOCK_TIMEOUT)
            self.__cursor = self.__connection.cursor()
            self._define_custom_functions()

    def reconnect(self) -> None:
        self.__connection = None
        self.__cursor = None
        if self.__db_path is None:
            raise RuntimeError("DB not connected!")
        self.__connection = sqlite3.connect(self.__db_path, timeout=self.DB_LOCK_TIMEOUT)
        self.__cursor = self.__connection.cursor()
        self._define_custom_functions()

    def get_db_path(self) -> Optional[str]:
        return self.__db_path

    def execute(self, query: str, params: Union[Dict, Sequence] = ()) -> sqlite3.Cursor:
        if self.__cursor is None:
            raise RuntimeError("DB not connected!")
        res = self.__cursor.execute(query, params)
        res.row_factory = sqlite3.Row  # type:ignore
        return res

    def executemany(self, query: str, params: Union[Dict, Sequence] = ()) -> sqlite3.Cursor:
        if self.__cursor is None:
            raise RuntimeError("DB not connected!")
        res = self.__cursor.executemany(query, params)
        res.row_factory = sqlite3.Row  # type:ignore
        return res

    def executemany_by_chunks(
        self, query: str, params: Union[Dict, Sequence] = (), chunk_size: int = 100_000
    ) -> sqlite3.Cursor:
        if self.__cursor is None:
            raise RuntimeError("DB not connected!")
        if self.__connection is None:
            raise RuntimeError("DB not connected!")
        for i in range(0, len(params), chunk_size):
            self.__cursor.execute("BEGIN")
            res = self.__cursor.executemany(query, params[i : i + chunk_size])
            self.__connection.commit()
        res.row_factory = sqlite3.Row  # type:ignore
        return res

    def iter_rows(
        self, query: str, params: Union[Dict, Sequence] = (), batch_size: int = 10_000
    ) -> Iterator[sqlite3.Row]:
        """Execute query on a dedicated cursor and yield rows lazily."""
        if self.__connection is None:
            raise RuntimeError("DB not connected!")
        conn = self.__connection
        old_factory = conn.row_factory
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                yield from rows
        finally:
            cursor.close()
            conn.row_factory = old_factory

    def execute_script(self, query: str) -> sqlite3.Cursor:
        if self.__cursor is None:
            raise RuntimeError("DB not connected!")
        return self.__cursor.executescript(query)

    def count_rows(self, table: str) -> int:
        return int(self.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def commit(self) -> None:
        if self.__connection is not None:
            self.__connection.commit()

    def disconnect(self) -> None:
        if self.__connection is not None:
            self.commit()
            self.__connection.close()
            self.__connection = None
            self.__cursor = None

    def get_lastrowid(self) -> Optional[int]:
        if self.__cursor is None:
            raise RuntimeError("DB not connected!")
        return self.__cursor.lastrowid

    def backup(self, database_path: str) -> None:
        if self.__connection:
            self.commit()
            dst = sqlite3.connect(database_path)
            self.__connection.backup(dst)
            dst.close()

    def extend_db(self, database_path: str) -> None:
        if not self.__connection:
            return
        self.commit()
        src = sqlite3.connect(database_path)
        src.row_factory = sqlite3.Row
        tables = src.execute("SELECT * FROM sqlite_master").fetchall()
        for table in tables:
            name = table["name"]
            self.execute(table["sql"])
            data = src.execute(f"SELECT * FROM {name}")
            for row in data:
                cols = row.keys()
                self.execute(
                    f"""
                    INSERT INTO {name} ({",".join(cols)})
                    VALUES ({",".join("?" * len(cols))})
                """,
                    [row[col] for col in cols],
                )
        src.close()
        self.commit()

    def create_tables(self, analysis: AnalysisType) -> None:
        base_path = os.path.dirname(os.path.dirname(__file__))
        sql_path = "sql/bridge.sql"
        if is_in_pyinstaller_env():
            base_path = get_pyinstaller_path()
        with open(os.path.join(base_path, sql_path), "r") as file:
            self.execute_script(file.read())

    def table_exists(self, table_name: str) -> bool:
        return (
            len(
                self.execute(
                    """
                        SELECT name
                        FROM sqlite_master
                        WHERE type="table" AND name=?
                        """,
                    (table_name,),
                ).fetchall()
            )
            > 0
        )

    def table_has_data(self, table_name: str) -> bool:
        if not self.table_exists(table_name):
            return False
        return self.execute(f"SELECT EXISTS(SELECT 1 FROM {table_name} LIMIT 1)").fetchone()[0]

    def get_kernel_attr(self, attr: str) -> Any:
        if not self.table_exists("KernelInfo"):
            raise RuntimeError("KernelInfo table is empty!")
        return self.execute(
            """
            SELECT KernelInfoVal
            FROM KernelInfo
            WHERE KernelInfoAttr=:attr
            """,
            {"attr": attr},
        ).fetchone()[0]

    def get_kernel_name(self) -> Any:
        return self.get_kernel_attr("name")

    def get_instr_num(self) -> Any:
        val = self.get_kernel_attr("instr_num")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_object_path(self) -> Any:
        return self.get_kernel_attr("object_path")

    def get_instrs_exec_num(self) -> Any:
        val = self.get_kernel_attr("instrs_exec_num")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_exec_time_ticks(self) -> Any:
        val = self.get_kernel_attr("exec_time_ticks")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_exec_time_ns(self) -> Any:
        val = self.get_kernel_attr("exec_time_ns")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_start_pc(self) -> Any:
        val = self.get_kernel_attr("start_pc")
        if val is not None:
            try:
                return int(val, 0) if isinstance(val, str) else int(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_arch(self) -> Any:
        raw_arch = self.get_kernel_attr("arch")
        if raw_arch is None:
            return None
        raw_arch = str(raw_arch)
        return CONTRACT_ARCH_ALIASES.get(raw_arch, raw_arch)

    def get_ai_core_active_count(self) -> Any:
        val = self.get_kernel_attr("ai_core_active")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
        return val

    def get_frequency_ghz(self) -> Any:
        val = self.get_kernel_attr("frequency_ghz")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return val

    def get_analysis_status(self, analysis_name: str) -> Any:
        if not self.table_exists("AnalysisState"):
            raise RuntimeError("AnalysisState table is empty!")
        analysis_state = self.execute(
            """
        SELECT AnalysisPassed
        FROM AnalysisState
        WHERE AnalysisName = :analysis_name
        """,
            {"analysis_name": analysis_name},
        ).fetchone()
        if not analysis_state:
            return False
        return analysis_state["AnalysisPassed"]

    def get_hint_status(self, hint_name: str) -> Any:
        if not self.table_exists("HintTypes"):
            raise RuntimeError("HintTypes table is empty!")
        hint_state = self.execute(
            """
        SELECT HintPassed
        FROM HintTypes
        WHERE HintTypeName = :hint_name
        """,
            {"hint_name": hint_name},
        ).fetchone()
        if not hint_state:
            return False
        return hint_state["HintPassed"]


database_handler = DatabaseHandler()
