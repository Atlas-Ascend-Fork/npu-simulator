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


import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from npusim.core.record.runtime import _dedupe_existing_files
from npusim.core.record.ktest.runtime import (
    prepare_runtime_dirs,
    _find_helper_lib,
    build_execution_env,
)

# py3.10 的 mock 逐级 getattr 解析字符串目标，会被 __init__ 里
# from-import 的同名函数遮蔽（npusim.core.record / ktest），改用 patch.object。
_ktest_runtime_mod = importlib.import_module("npusim.core.record.ktest.runtime")


class KtestRuntimePrepareDirsTests(unittest.TestCase):
    def test_prepare_runtime_dirs_creates_log_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target_dir = Path(tmp) / "target"
            ok = prepare_runtime_dirs(target_dir)
            self.assertTrue(ok)
            self.assertTrue((target_dir / "log").exists())

    def test_prepare_runtime_dirs_failure(self):
        ok = prepare_runtime_dirs(Path("/dev/null/nonexistent"))
        self.assertFalse(ok)


class KtestDedupeTests(unittest.TestCase):
    def test_dedupe_removes_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.txt"
            a.write_text("")
            result = list(_dedupe_existing_files([a, a, None]))
            self.assertEqual(len(result), 1)

    def test_dedupe_skips_missing(self):
        result = list(_dedupe_existing_files([Path("/nonexistent")]))
        self.assertEqual(result, [])


class KtestFindHelperTests(unittest.TestCase):
    def test_find_helper_lib_not_found(self):
        with patch.dict(os.environ, {"ASCEND_TOOLKIT_HOME": "/nonexistent"}):
            result = _find_helper_lib({}, Path("/camodel"))
            self.assertIsNone(result)

    def test_find_helper_lib_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / "libcannsim_helper.so"
            lib.write_text("")
            env = {"CANNSIM_HELPER_LIB": str(lib)}
            result = _find_helper_lib(env, Path("/camodel"))
            self.assertEqual(result, lib.resolve())


class KtestBuildEnvTests(unittest.TestCase):
    def test_build_env_sets_arch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = root / "libcannsim_helper.so"
            helper.write_text("", encoding="utf-8")
            camodel_dir = root / "camodel"
            camodel_dir.mkdir()
            record_dir = root / "record"
            record_dir.mkdir()
            with patch.object(_ktest_runtime_mod, "_find_helper_lib", return_value=helper):
                env = build_execution_env({"ASCEND_TOOLKIT_HOME": str(root)}, camodel_dir, record_dir)
            self.assertEqual(env["CANNSIM_ARCH"], "Ascend950")

    def test_build_env_missing_helper_raises(self):
        from npusim.core.record.paths import RecordPathError

        with tempfile.TemporaryDirectory() as tmp:
            camodel_dir = Path(tmp) / "camodel"
            camodel_dir.mkdir()
            record_dir = Path(tmp) / "record"
            record_dir.mkdir()
            with patch.object(_ktest_runtime_mod, "_find_helper_lib", return_value=None):
                with self.assertRaises(RecordPathError):
                    build_execution_env({}, camodel_dir, record_dir)


class EnsureSimExecutableTests(unittest.TestCase):
    def test_ensure_sim_executable_missing(self):
        from npusim.core.record.ktest.runtime import ensure_sim_executable

        ok = ensure_sim_executable(Path("/nonexistent/sim"))
        self.assertFalse(ok)

    def test_ensure_sim_executable_valid(self):
        from npusim.core.record.ktest.runtime import ensure_sim_executable

        with tempfile.TemporaryDirectory() as tmp:
            sim = Path(tmp) / "sim"
            sim.write_text("#!/bin/sh\necho ok")
            ok = ensure_sim_executable(sim)
            self.assertTrue(ok)


class SymlinkTests(unittest.TestCase):
    def test_create_case_symlink(self):
        from npusim.core.record.ktest.runtime import create_case_symlink
        from npusim.core.record.ktest.runtime import remove_case_symlink

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case_dir"
            case_dir.mkdir()
            symlink = Path(tmp) / "case"
            ok = create_case_symlink(symlink, case_dir)
            self.assertTrue(ok)
            self.assertTrue(symlink.is_symlink())
            # Clean up
            remove_case_symlink(symlink)
            self.assertFalse(symlink.is_symlink())

    def test_create_case_symlink_nonexistent_dir(self):
        from npusim.core.record.ktest.runtime import create_case_symlink

        ok = create_case_symlink(Path("/tmp/case"), Path("/nonexistent"))
        self.assertFalse(ok)

    def test_remove_case_symlink_none(self):
        from npusim.core.record.ktest.runtime import remove_case_symlink

        remove_case_symlink(None)  # should not crash


class DetectRuntimeConfigTests(unittest.TestCase):
    def test_detect_missing_top_json(self):
        from npusim.core.record.ktest.runtime import detect_runtime_config

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            with self.assertRaises(ValueError):
                detect_runtime_config(case_dir)

    def test_detect_1p(self):
        from npusim.core.record.ktest.runtime import detect_runtime_config
        import json

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            (case_dir / "top.json").write_text(json.dumps({"chip0": {}}))
            config = detect_runtime_config(case_dir)
            self.assertEqual(config, "1p")

    def test_detect_2p(self):
        from npusim.core.record.ktest.runtime import detect_runtime_config
        import json

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            (case_dir / "top.json").write_text(json.dumps({"chip0": {}, "chip1": {}}))
            config = detect_runtime_config(case_dir)
            self.assertEqual(config, "2p")

    def test_detect_invalid_top_json(self):
        from npusim.core.record.ktest.runtime import detect_runtime_config

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            (case_dir / "top.json").write_text("not json")
            with self.assertRaises(ValueError):
                detect_runtime_config(case_dir)

    def test_detect_no_chip_keys(self):
        from npusim.core.record.ktest.runtime import detect_runtime_config
        import json

        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "case"
            case_dir.mkdir()
            (case_dir / "top.json").write_text(json.dumps({"host": {}}))
            with self.assertRaises(ValueError):
                detect_runtime_config(case_dir)


class ResolveConfigTemplateTests(unittest.TestCase):
    def test_resolve_1p_returns_none(self):
        from npusim.core.record.ktest.runtime import resolve_config_template

        self.assertIsNone(resolve_config_template("1p"))


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
