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


import tempfile
import unittest
from pathlib import Path

from npusim.core.record.launch_spec import (
    _is_elf,
    build_launch_spec,
)


class LaunchSpecTests(unittest.TestCase):
    def test_is_elf_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bin"
            path.write_bytes(b"\x7fELF" + b"\x00" * 8)
            self.assertTrue(_is_elf(path))

    def test_is_elf_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "text"
            path.write_text("not elf", encoding="utf-8")
            self.assertFalse(_is_elf(path))

    def test_is_elf_missing_file(self):
        self.assertFalse(_is_elf(Path("/nonexistent/file")))

    def test_binary_file_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / "myapp"
            binary.write_bytes(b"\x7fELF" + b"\x00" * 8)
            spec = build_launch_spec(str(binary), "", root)
            self.assertEqual(spec.kind, "binary")
            self.assertEqual(spec.label, "myapp")
            self.assertEqual(spec.argv[0], str(binary.resolve()))

    def test_script_file_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "run.sh"
            script.write_text("#!/bin/sh\necho hi", encoding="utf-8")
            spec = build_launch_spec(str(script), "", root)
            self.assertEqual(spec.kind, "script")
            self.assertTrue(spec.argv[0].endswith("bash"))

    def test_command_launch(self):
        spec = build_launch_spec("ls -la", "", Path("/tmp"))
        self.assertEqual(spec.kind, "command")
        self.assertEqual(spec.label, "ls")

    def test_command_with_python_interpreter(self):
        spec = build_launch_spec("python3 train.py", "", Path("/tmp"))
        self.assertEqual(spec.kind, "command")
        self.assertEqual(spec.label, "train.py")
        self.assertTrue(spec.is_python)

    def test_command_with_env(self):
        spec = build_launch_spec("env FOO=1 ./app", "", Path("/tmp"))
        self.assertEqual(spec.kind, "command")

    def test_command_with_shell_metachars(self):
        spec = build_launch_spec("echo a | grep a", "", Path("/tmp"))
        self.assertEqual(spec.kind, "command")

    def test_user_option_appended_to_command(self):
        spec = build_launch_spec("ls", "--color=auto", Path("/tmp"))
        self.assertIn("--color=auto", spec.argv[-1])

    def test_safe_archive_label_truncates(self):
        spec = build_launch_spec("python3 " + "x" * 100 + ".py", "", Path("/tmp"))
        self.assertLessEqual(len(spec.label), 64)


if __name__ == "__main__":
    unittest.main()
