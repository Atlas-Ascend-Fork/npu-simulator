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

"""Smoke tests for the ported npusim.prof package (former npu_emulate)."""

import unittest


class ProfPackageTests(unittest.TestCase):
    def test_package_importable(self):
        import npusim.prof  # noqa: F401

    def test_version_aliases_single_source(self):
        import npusim.version as npusim_version
        import npusim.prof.src.version as prof_version

        self.assertEqual(prof_version.MAJOR_VERSION, npusim_version.MAJOR_VERSION)
        self.assertEqual(prof_version.MINOR_VERSION, npusim_version.MINOR_VERSION)
        self.assertEqual(prof_version.PATCH_VERSION, npusim_version.PATCH_VERSION)
        self.assertEqual(prof_version.VERSION, npusim_version.VERSION)

    def test_no_standalone_npu_emulate_references(self):
        """The port must not leave importable npu_emulate references behind."""
        from pathlib import Path

        prof_root = Path(__file__).resolve().parents[2] / "npusim" / "prof"
        offenders = [
            str(p.relative_to(prof_root))
            for p in prof_root.rglob("*.py")
            if "npu_emulate" in p.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(offenders, [])

    def test_static_and_sql_data_files_present(self):
        from pathlib import Path

        prof_root = Path(__file__).resolve().parents[2] / "npusim" / "prof"
        sql_dir = prof_root / "src" / "sql"
        sql_files = sorted(p.name for p in sql_dir.glob("*.sql"))
        self.assertEqual(sql_files, ["analysis.sql", "bridge.sql", "hints.sql"])
        svg_files = sorted(str(p.relative_to(prof_root)) for p in (prof_root / "static").rglob("*.svg"))
        self.assertEqual(
            svg_files,
            [
                "static/arch_diagrams/v100/aicore_memory_sim_view.svg",
            ],
        )


if __name__ == "__main__":
    unittest.main()
