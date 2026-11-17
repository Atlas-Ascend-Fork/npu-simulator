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

import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from npusim.core.public import aicore_objects as objects


def device_elf(machine=0x1029):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0, 0, 68, 0, 64, 0, 0, 64, 2, 0)
    code = b"\0" * 4
    section = struct.pack("<IIQQQQIIQQ", 0, 1, 6, 0, 64, 4, 0, 0, 4, 0)
    return ident + header + code + bytes(64) + section


class ToolTimeoutTests(unittest.TestCase):
    def test_timeout_kills_child_process_group(self):
        with patch.object(objects.subprocess, "Popen") as popen, patch.object(objects.os, "killpg") as kill:
            process = popen.return_value.__enter__.return_value
            process.pid = 12345
            process.communicate.side_effect = [subprocess.TimeoutExpired("tool", 1), ("", "")]
            with self.assertRaises(subprocess.TimeoutExpired):
                objects._run_tool(["tool"], 1, {})
            kill.assert_called_once_with(12345, objects.signal.SIGKILL)
            self.assertEqual(process.communicate.call_count, 2)
            self.assertTrue(popen.call_args[1]["start_new_session"])


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / "app with spaces"
        self.app.write_bytes(device_elf(183))
        self.dest = self.root / "aicore_objects"
        self.tools = patch.object(objects, "_tools", return_value=(Mock(return_value="msobjdump"), lambda: {}))
        self.tools.start()
        self.addCleanup(self.tools.stop)

    def extracted(self, argv, **kwargs):
        self.assertEqual(kwargs["timeout"], 120)
        self.assertEqual(argv[2], str(self.app))
        (Path(argv[-1]) / "tool named object.o").write_bytes(device_elf())
        return subprocess.CompletedProcess(argv, 0, "", "")

    def test_success_validates_and_archives(self):
        with patch.object(objects, "_run_tool", side_effect=self.extracted):
            self.assertTrue(objects.extract_device_objects(self.app, self.dest))
        self.assertEqual(objects.archive_candidates(self.root), [(self.dest / "device_0000.o").resolve()])
        self.assertFalse((self.dest / ".incomplete").exists())

    def test_failures_never_publish_partial_output(self):
        for failure in ("nonzero", "timeout", "invalid", "empty", "host", "mixed", "symlink"):
            with self.subTest(failure=failure):

                def run(argv, **kwargs):
                    target = Path(argv[-1]) / "partial.o"
                    if failure == "timeout":
                        target.write_bytes(device_elf())
                        raise subprocess.TimeoutExpired(argv, 120)
                    if failure == "symlink":
                        target.symlink_to(self.app)
                    elif failure != "empty":
                        target.write_bytes(
                            device_elf(183)
                            if failure == "host"
                            else b"\x7fELF"
                            if failure in ("invalid", "mixed")
                            else device_elf()
                        )
                    if failure == "mixed":
                        (target.parent / "valid.o").write_bytes(device_elf())
                    return subprocess.CompletedProcess(argv, 1 if failure == "nonzero" else 0, "", "bad\noutput")

                with patch.object(objects, "_run_tool", side_effect=run):
                    self.assertFalse(objects.extract_device_objects(self.app, self.dest))
                self.assertFalse(self.dest.exists())

    def test_missing_tool_and_os_error_warn_only(self):
        with patch.object(objects, "_tools", return_value=(Mock(return_value=None), lambda: {})):
            self.assertFalse(objects.extract_device_objects(self.app, self.dest))
        with patch.object(objects, "_run_tool", side_effect=OSError("unavailable")):
            self.assertFalse(objects.extract_device_objects(self.app, self.dest))

    def test_existing_destination_and_symlink_not_overwritten(self):
        self.dest.mkdir()
        sentinel = self.dest / "keep"
        sentinel.write_text("unchanged")
        with patch.object(objects, "_run_tool") as run:
            self.assertFalse(objects.extract_device_objects(self.app, self.dest))
        run.assert_not_called()
        self.assertEqual(sentinel.read_text(), "unchanged")
        link = self.root / "link"
        link.symlink_to(self.dest)
        self.assertFalse(objects.extract_device_objects(self.app, link))

    def test_copy_failure_keeps_archive_unusable(self):
        with patch.object(objects, "_run_tool", side_effect=self.extracted), patch.object(
            objects.shutil, "copyfileobj", side_effect=OSError("disk full")
        ):
            self.assertFalse(objects.extract_device_objects(self.app, self.dest))
        self.assertTrue((self.dest / ".incomplete").exists())
        self.assertEqual(objects.archive_candidates(self.root), [])

    def test_validation_rejects_truncation_host_and_symlink(self):
        for data in (b"not ELF", device_elf()[:-1], device_elf(183)):
            self.app.write_bytes(data)
            self.assertFalse(objects.is_device_elf(self.app))
        self.app.write_bytes(device_elf())
        self.assertTrue(objects.is_device_elf(self.app))
        link = self.root / "object.o"
        link.symlink_to(self.app)
        self.assertFalse(objects.is_device_elf(link))


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / "record" / "aicore_objects"
        self.archive.mkdir(parents=True)
        self.a = self.archive / "a.o"
        self.b = self.archive / "b.o"
        self.a.write_bytes(device_elf())
        self.b.write_bytes(device_elf())

    def resolver(self, root=None, explicit=None):
        resolver = objects.DeviceObjectResolver(root or self.root, explicit)
        self.addCleanup(resolver.close)
        return resolver

    def test_one_elf_multiple_kernels_and_different_elfs(self):
        indices = {self.a: {"alpha": {"_Z5alphav"}, "beta": {"_Z4betav"}}, self.b: {"gamma": {"gamma"}}}
        with patch.object(objects, "object_symbols", side_effect=lambda p: indices[p]):
            for root in (self.root, self.root / "record"):
                resolver = self.resolver(root)
                self.assertEqual(resolver.select("alpha"), objects.DeviceObject(str(self.a), "_Z5alphav"))
                self.assertEqual(resolver.select("beta"), objects.DeviceObject(str(self.a), "_Z4betav"))
                self.assertEqual(resolver.select("gamma"), objects.DeviceObject(str(self.b), "gamma"))
                self.assertIsNone(resolver.select("alp"))

    def test_single_elf_with_two_overloads_is_ambiguous(self):
        with patch.object(objects, "object_symbols", return_value={"alpha": {"_Z5alphai", "_Z5alphaf"}}):
            self.assertIsNone(self.resolver(explicit=str(self.a)).select("alpha"))

    def test_legacy_device_marker_and_chip_layout(self):
        old = self.root / "legacy"
        old.mkdir()
        (old / ".user-app").write_text(str(self.a) + "\n")
        with patch.object(objects, "object_symbols", side_effect=lambda p: {"alpha": {"alpha"}} if p == self.a else {}):
            self.assertEqual(self.resolver(old).select("alpha"), objects.DeviceObject(str(self.a), "alpha"))
            self.assertEqual(
                self.resolver(self.root / "record" / "chip0").select("alpha"),
                objects.DeviceObject(str(self.a), "alpha"),
            )

    def test_symbol_tool_failure_timeout_and_missing_are_safe(self):
        with patch.object(objects, "_tools", return_value=(Mock(return_value="objdump"), lambda: {})):
            for error in (OSError("missing"), subprocess.TimeoutExpired("objdump", 30)):
                with patch.object(objects, "_run_tool", side_effect=error):
                    self.assertEqual(objects.object_symbols(self.a), {})
            with patch.object(objects, "_run_tool", return_value=subprocess.CompletedProcess([], 1, "", "error")):
                self.assertEqual(objects.object_symbols(self.a), {})
        with patch.object(objects, "_tools", return_value=(Mock(return_value=None), lambda: {})):
            self.assertEqual(objects.object_symbols(self.a), {})

    def test_duplicate_kernel_and_overload_are_ambiguous(self):
        for names in ({"alpha": {"alpha"}}, {"alpha": {"_Z5alphai", "_Z5alphaf"}}):
            with patch.object(objects, "object_symbols", return_value=names), patch.object(
                objects, "print_warn"
            ) as warn:
                self.assertIsNone(self.resolver().select("alpha"))
                warn.assert_called()

    def test_explicit_overrides_archive(self):
        explicit = self.root / "explicit.o"
        explicit.write_bytes(device_elf())
        with patch.object(objects, "object_symbols", return_value={"alpha": {"alpha"}}):
            self.assertEqual(
                self.resolver(explicit=str(explicit)).select("alpha"), objects.DeviceObject(str(explicit), "alpha")
            )

    def test_copied_archive_survives_missing_app(self):
        (self.root / "record" / ".user-app").write_text(str(self.root / "missing app") + "\n")
        copied = self.root / "copied"
        shutil.copytree(self.root / "record", copied)
        with patch.object(
            objects, "object_symbols", side_effect=lambda p: {"alpha": {"alpha"}} if p.name == "a.o" else {}
        ):
            self.assertEqual(
                self.resolver(copied).select("alpha"),
                objects.DeviceObject(str(copied / "aicore_objects" / "a.o"), "alpha"),
            )

    def test_old_marker_host_is_extracted_not_used_directly(self):
        old = self.root / "old"
        old.mkdir()
        host = old / "app"
        host.write_bytes(device_elf(183))
        (old / ".user-app").write_text(str(host) + "\n")

        def extract(app, dest):
            self.assertEqual(app, host)
            dest.mkdir()
            (dest / "device.o").write_bytes(device_elf())
            return True

        with patch.object(objects, "extract_device_objects", side_effect=extract), patch.object(
            objects, "object_symbols", return_value={"alpha": {"alpha"}}
        ):
            selected = self.resolver(old).select("alpha")
        self.assertNotEqual(selected[0], str(host))
        self.assertTrue(objects.is_device_elf(Path(selected[0])))

    def test_invalid_or_incomplete_archive_not_used(self):
        (self.archive / ".incomplete").touch()
        with patch.object(objects, "object_symbols") as symbols:
            self.assertIsNone(self.resolver().select("alpha"))
        symbols.assert_not_called()

    def test_raw_demangled_symbols_are_exact_and_keep_overloads(self):
        raw = "0 g F .text 10 _Z5alphav\n10 g F .text 10 _Z5alphai\n20 l F .text 10 helper\n"
        demangled = "0 g F .text 10 alpha()\n10 g F .text 10 alpha(int)\n20 l F .text 10 helper\n"
        with patch.object(objects, "_tools", return_value=(Mock(return_value="objdump"), lambda: {})), patch.object(
            objects,
            "_run_tool",
            side_effect=[subprocess.CompletedProcess([], 0, raw), subprocess.CompletedProcess([], 0, demangled)],
        ):
            index = objects.object_symbols(self.a)
        self.assertEqual(index["alpha"], {"_Z5alphav", "_Z5alphai"})
        self.assertNotIn("alp", index)
        self.assertNotIn("helper", index)
