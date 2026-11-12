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

"""Tests for the BiProf hotspot reporter's per-DB handling."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from npusim.prof.src.backend.reporters import HotspotsDbOnlyReporter


class EmptyKernelDbTests(unittest.TestCase):
    def test_skip_db_without_executed_instructions(self):
        """Warmup kernels produce DBs with zero instructions; report must skip them."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "0000_warmup.db"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE KernelInfo (KernelInfoAttr TEXT, KernelInfoVal TEXT)")
            conn.execute("CREATE TABLE ExecutedInstructions (ExecInstrId INTEGER)")
            conn.commit()
            conn.close()

            ret = HotspotsDbOnlyReporter()._process_one_db(str(db_path), 0)

            self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
