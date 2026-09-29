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

import contextlib
import importlib.util
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _load_secret_scan():
    spec = importlib.util.spec_from_file_location("secret_scan", ".gitcode/scripts/secret_scan.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SecretScanLegacyPathTests(unittest.TestCase):
    def setUp(self):
        self.scanner = _load_secret_scan()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.current = self.root / "npusim"
        self.current.mkdir()
        self.legacy = self.root / "cannsim"
        script = self.root / ".gitcode" / "scripts" / "secret_scan.py"
        patch = mock.patch.object(self.scanner, "__file__", str(script))
        patch.start()
        self.addCleanup(patch.stop)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)

    def _run(self, paths):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = self.scanner.main([str(path) for path in paths] + ["--no-allowlist"])
        return result, output.getvalue()

    def _track(self, path, content):
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", str(path)], check=True)

    def test_legacy_path_scans_actual_tracked_source(self):
        self._track(self.current / "sample.py", "value = 1\n")
        result, output = self._run([self.legacy])
        self.assertEqual(result, 0)
        self.assertIn("Compatibility: cannsim -> npusim", output)
        self.assertIn("Files to scan: 1", output)
        self.assertIn("Scanning: npusim", output)

    def test_legacy_path_does_not_hide_secrets(self):
        self._track(self.current / "sample.py", 'password="not-a-real-password"\n')
        result, output = self._run([self.legacy])
        self.assertEqual(result, 1)
        self.assertIn("npusim/sample.py:1:", output)
        self.assertIn("credential_assignment", output)

    def test_existing_legacy_directory_is_not_redirected(self):
        self.legacy.mkdir()
        self._track(self.legacy / "sample.py", 'password="not-a-real-password"\n')
        result, output = self._run([self.legacy])
        self.assertEqual(result, 1)
        self.assertNotIn("Compatibility:", output)
        self.assertIn("cannsim/sample.py:1:", output)

    def test_missing_current_directory_still_fails(self):
        self.current.rmdir()
        result, output = self._run([self.legacy])
        self.assertEqual(result, 2)
        self.assertIn("scan path does not exist", output)

    def test_other_missing_path_still_fails(self):
        result, output = self._run([self.legacy, self.root / "missing"])
        self.assertEqual(result, 2)
        self.assertIn("scan path does not exist", output)

    def test_unrelated_cannsim_path_is_not_redirected(self):
        result, output = self._run([self.root / "other" / "cannsim"])
        self.assertEqual(result, 2)
        self.assertNotIn("Compatibility:", output)

    def test_alias_and_current_path_do_not_duplicate_files(self):
        self._track(self.current / "sample.py", "value = 1\n")
        result, output = self._run([self.legacy, self.current])
        self.assertEqual(result, 0)
        self.assertIn("Files to scan: 1", output)


class SecretScanRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = _load_secret_scan()
        cls.patterns = cls.scanner.compile_patterns(cls.scanner.re)

    def _findings(self, text):
        return list(self.scanner.scan_text(text, self.patterns, "test.txt", 1, text))

    def test_url_and_url_ip_ignore_xml_namespace_context(self):
        findings = self._findings('<svg xmlns="http://www.w3.org/2000/svg" width="16"></svg>')
        rules = {finding.rule for finding in findings}
        self.assertNotIn("url", rules)
        self.assertNotIn("url_ip", rules)

    def test_regular_urls_are_still_reported(self):
        findings = self._findings('visit "https://example.invalid/api"')
        self.assertIn("url", {finding.rule for finding in findings})
        self.assertIn("url_ip", {finding.rule for finding in findings})


class SecretScanAllowlistTests(unittest.TestCase):
    def _load(self, content):
        scanner = _load_secret_scan()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowlist.txt"
            path.write_text(content, encoding="utf-8")
            return scanner.load_allowlist(path, scanner.re)

    def test_scoped_allowlist(self):
        entries = self._load(
            "rule=url path=scripts/pre_smoke_for_action.sh value=https://mirrors.aliyun.com/pytorch-wheels/cpu\n"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["rule"], "url")
        self.assertEqual(entries[0]["value"], "https://mirrors.aliyun.com/pytorch-wheels/cpu")

    def test_legacy_allowlist(self):
        entries = self._load("mirrors.aliyun.com\n")
        self.assertEqual(len(entries), 1)
        self.assertIn("legacy_literal", entries[0])

    def test_scoped_path_normalization_preserves_hidden_directories(self):
        entries = self._load("rule=url path=.gitcode/scripts/pre_smoke_for_action.sh value=https://example.invalid\n")
        self.assertEqual(entries[0]["path"], ".gitcode/scripts/pre_smoke_for_action.sh")

    def test_scoped_allowlist_suppresses_exact_url_only(self):
        allowlist = self._load(
            "rule=url path=scripts/pre_smoke_for_action.sh value=https://mirrors.aliyun.com/pytorch-wheels/cpu\n"
        )
        scanner = _load_secret_scan()
        url_finding = scanner.Finding(
            "scripts/pre_smoke_for_action.sh",
            1,
            1,
            "url",
            "https://mirrors.aliyun.com/pytorch-wheels/cpu",
            'TORCH_MIRROR="https://mirrors.aliyun.com/pytorch-wheels/cpu"',
        )
        credential_finding = scanner.Finding(
            "scripts/pre_smoke_for_action.sh",
            1,
            1,
            "credential_assignment",
            "password=not-the-mirror",
            'password="not-the-mirror"',
        )
        self.assertTrue(scanner.is_allowed(url_finding, allowlist))
        self.assertFalse(scanner.is_allowed(credential_finding, allowlist))
