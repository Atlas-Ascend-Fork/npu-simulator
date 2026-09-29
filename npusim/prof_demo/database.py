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
"""Read-only simulation facts with connection-local, in-memory analysis."""

import math
from pathlib import Path
import sqlite3

from npusim.prof_demo.config import config


class DatabaseHandler:
    def __init__(self):
        self.connection = None

    def connect(self, path):
        if self.connection is not None:
            raise RuntimeError("A report session is already connected")
        # Enable URI interpretation explicitly for the attached mode=ro DB,
        # including SQLite builds that do not enable URI filenames by default.
        self.connection = sqlite3.connect(":memory:", uri=True)
        self.connection.row_factory = sqlite3.Row
        try:
            uri = Path(path).resolve().as_uri() + "?mode=ro"
            self.execute("ATTACH DATABASE ? AS sim", (uri,))
            self._validate()
            self.execute("CREATE TABLE ExecutedInstructionsAdjusted AS SELECT * FROM sim.ExecutedInstructions")
            self.execute("CREATE UNIQUE INDEX idx_instr_id ON ExecutedInstructionsAdjusted(ExecInstrId)")
            self.execute("CREATE VIEW ExecutedInstructions AS SELECT * FROM ExecutedInstructionsAdjusted")
            self.connection.executescript(Path(__file__).with_name("schema.sql").read_text())
        except Exception:
            self.disconnect()
            raise

    def _validate(self):
        if self.get_arch() != "v100":
            raise ValueError("Only v100 databases are supported")
        # v6 introduced decoder facts from which these reports derive metrics.
        version = int(self.get_kernel_attr("contract_version"))
        if version not in (6, 7):
            raise ValueError(f"Unsupported DB contract_version: {version}; expected 6 or 7")
        frequency = float(self.get_frequency_ghz())
        if not math.isfinite(frequency) or frequency <= 0:
            raise ValueError("KernelInfo.frequency_ghz must be finite and positive")
        if int(self.get_exec_time_ticks()) <= 0:
            raise ValueError("KernelInfo.exec_time_ticks must be positive")
        if not self.count_rows("sim.ExecutedInstructions"):
            raise ValueError("No executed instructions found")
        invalid = self.execute("""
            SELECT 1 FROM sim.ExecutedInstructions
            WHERE ExecInstrTickStart < 0 OR ExecInstrTickEnd < ExecInstrTickStart
               OR CoreTypeId NOT IN (1, 2, 3)
            LIMIT 1
        """).fetchone()
        if invalid:
            raise ValueError("Invalid instruction timing or core type")
        timing = self.execute(
            "SELECT MIN(ExecInstrTickStart), MAX(ExecInstrTickEnd) FROM sim.ExecutedInstructions"
        ).fetchone()
        if timing[1] <= timing[0]:
            raise ValueError("Instruction span must be positive")

    def execute(self, sql, params=()):
        return self.connection.execute(sql, params)

    def executemany(self, sql, params):
        return self.connection.executemany(sql, params)

    def iter_rows(self, sql, params=()):
        cursor = self.execute(sql, params)
        try:
            yield from cursor
        finally:
            cursor.close()

    def count_rows(self, table):
        return self.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def commit(self):
        self.connection.commit()

    def enable_query_only(self):
        self.commit()
        self.execute("PRAGMA query_only = ON")

    def disconnect(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def get_kernel_attr(self, attr):
        row = self.execute("SELECT KernelInfoVal FROM sim.KernelInfo WHERE KernelInfoAttr=?", (attr,)).fetchone()
        if row is None:
            raise ValueError(f"Missing KernelInfo attribute: {attr}")
        return row[0]

    def get_arch(self):
        return self.get_kernel_attr("arch")

    def get_frequency_ghz(self):
        return self.get_kernel_attr("frequency_ghz")

    def get_exec_time_ticks(self):
        return self.get_kernel_attr("exec_time_ticks")


class BackendDBManager:
    @staticmethod
    def get_active_cores():
        available = [
            r[0]
            for r in database_handler.execute(
                "SELECT DISTINCT ExecInstrCoreId FROM ExecutedInstructions ORDER BY ExecInstrCoreId"
            )
        ]
        if config.core_id == "all":
            return available
        requested = set()
        try:
            for part in config.core_id.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    if start < 0 or end < start:
                        raise ValueError
                    requested.update(core for core in available if start <= core <= end)
                else:
                    core = int(part)
                    if core < 0:
                        raise ValueError
                    requested.add(core)
        except ValueError as exc:
            raise ValueError("core-id must be 'all' or nonnegative IDs/ranges, e.g. 0-2,5") from exc
        selected = sorted(requested.intersection(available))
        if not selected:
            raise ValueError("No selected core exists in the input DB")
        return selected

    @staticmethod
    def get_active_core_types():
        return [
            r[0]
            for r in database_handler.execute(
                "SELECT DISTINCT CoreTypeId FROM ExecutedInstructions ORDER BY CoreTypeId"
            )
        ]


database_handler = DatabaseHandler()
