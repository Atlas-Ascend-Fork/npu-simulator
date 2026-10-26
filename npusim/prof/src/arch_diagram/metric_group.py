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

import logging as log
import glob
import sqlite3
from dataclasses import dataclass
from typing import List, Dict
from collections import defaultdict
from npusim.prof.src.backend.database import database_handler


class DatabaseManager:
    @staticmethod
    def get_metrics_row(metric_names: List[str], table_name: str) -> List[sqlite3.Row]:
        if not database_handler.table_exists(table_name):
            return list()
        metrics = str(tuple(metric_names)).replace(",)", ")")
        sql_query = """
        SELECT ArchDiagramParameterName AS Metric,
               AVG(ArchDiagramParameterValue) AS Value
        FROM {}
        WHERE ArchDiagramParameterName IN {}
        GROUP BY ArchDiagramParameterName
        """.format(table_name, metrics)
        summary = database_handler.execute(sql_query)
        return summary.fetchall()


@dataclass
class MetricGroup:
    name: str
    db_name: str
    table_name: str
    metrics: tuple

    def get_metric_names(self) -> list:
        return [m.name for m in self.metrics]

    def get_total_metrics(self) -> int:
        return len(self.metrics)

    def get_db_name(self) -> str:
        return self.db_name

    def get_table_name(self) -> str:
        return self.table_name


class ArchDiagramMetricGroup:
    def __init__(self, metric_group: MetricGroup):
        self._metric_group: MetricGroup = metric_group

    def __str__(self) -> str:
        return f"{self._metric_group.name}"

    def debug_print_metrics(self):
        log.debug(f"    DB name: {self._metric_group.db_name}")
        metrics = self._metric_group.get_metric_names()
        log.debug(f"    Metrics as list: {metrics}")

        metrics = self._metric_group.metrics
        for i, m in enumerate(metrics):
            m.value = str(i)
            log.debug(f"    Metric name: {m.name} = {m.value}")

    def scan_db(self, input_dir: str) -> int:
        ret = 0
        metrics: Dict[str, float] = {}
        # use simulator metrics from biprof db
        ret += self.get_data_from_db(self._metric_group.db_name, metrics)
        ret += self.calculate_metrics(metrics)
        return ret

    def find_db(self, input_dir: str) -> list:
        db_files = glob.glob(f"{input_dir}/**/{self._metric_group.db_name}", recursive=True)
        return db_files

    def connect_to_db(self, db: str) -> int:
        if db != "":
            database_handler.connect(db)
        return 0

    def disconnect_from_db(self, db: str) -> int:
        if db != "":
            database_handler.disconnect()
        return 0

    def get_data_from_db(self, db: str, metrics: Dict[str, float]) -> int:
        self.connect_to_db(db)
        metric_names = self._metric_group.get_metric_names()
        table_name = self._metric_group.get_table_name()
        metric_row = DatabaseManager.get_metrics_row(metric_names, table_name)
        assert metric_row is not None
        metrics.update({m["Metric"]: m["Value"] for m in metric_row})
        assert len(metrics) == self._metric_group.get_total_metrics()
        self.disconnect_from_db(db)
        return 0

    def calculate_metrics(self, metric_values: dict) -> int:
        for metric in self._metric_group.metrics:
            metric.data = metric.format(metric_values[metric.name])
        return 0

    def get_metrics_dict(self) -> Dict[str, str]:
        metrics_dict: Dict[str, str] = defaultdict(str)
        for metric in self._metric_group.metrics:
            metrics_dict[metric.name] = metric.data
        return metrics_dict
