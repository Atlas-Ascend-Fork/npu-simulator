# -*- coding: utf-8 -*-
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
"""Security scanner for publishable repository content.

The scanner distinguishes two result classes:

* ``secret``: a technical rule matched and requires removal or scoped approval.
* ``review``: content cannot be classified automatically (images, LFS objects,
  chip terms) and requires an owner review. Review findings do not fail the
  default run, but can be made blocking with ``--strict-manual-review``.

Coverage: employee IDs (alpha-prefixed, and numeric inside an author header),
author-header personal names, e-mail addresses, private keys, credential
assignments and connection strings, private IPs, personal paths and internal
issue IDs. Tracked files are read through ``git ls-files`` so static assets,
SVG, extensionless files, binary strings, archives, image metadata, OOXML
document metadata and LFS pointers are all covered; paths outside a git
repository fall back to a filesystem walk.

Items that no rule can decide reliably - non-standard IDs such as ``swx13903``
or ``228413``, text rendered inside images, LFS object contents and chip terms -
are listed as manual review work and are repeated in the report as a residual
note, so a clean scan is never mistaken for a complete review. Document
metadata and files larger than one MiB are hard failures and need explicit
removal or scoped approval.

The third-party ``regex`` engine is preferred (it is what CI installs), but
every rule is written to also compile with the stdlib ``re`` module, so a plain
``python3 .gitcode/scripts/secret_scan.py`` run works too. Whichever engine is
used, all rules must compile: a rule that fails is a hard error, never a silent
skip.

Public CI intentionally prints only locations, rule names, and fingerprints.
Use ``--verbose`` locally when an authorized maintainer needs match context.

Allowlist entries are either scoped approvals::

    rule=<rule> path=<repo path> value=<exact match>

or the legacy entries that the existing ``secret_scan_allowlist.txt`` uses: a
plain substring, or ``regex:PATTERN``. Legacy entries are still honoured so the
repository keeps working without editing that file; they are intentionally
broader, so prefer scoped entries for new approvals.

Usage:
    python3 secret_scan.py [PATH ...] [--allowlist FILE] [--terms FILE]
                           [--include-untracked] [--strict-manual-review]
                           [--verbose] [--exit-zero] [--no-allowlist]

Exit codes:
    0  No blocking findings
    1  Secret finding found, or review finding in strict mode
    2  Scanner configuration or execution error
"""

import argparse
import fnmatch
import hashlib
import os
import re
import shlex
import stat
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
import zipfile
from collections import namedtuple
from datetime import datetime, timezone


class ConfigError(RuntimeError):
    """Raised when scanner configuration or rules are invalid."""


class ExecutionError(RuntimeError):
    """Raised when a file cannot be safely and completely inspected."""


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

_PRIVATE_KEY_MARKER = "-----BEGIN " + "(?:RSA |EC |DSA |OPENSSH )?" + "PRIVATE KEY-----"
_CERTIFICATE_MARKER = "-----BEGIN " + "CERTIFICATE-----"
# CJK range instead of \p{Han} so the rule also compiles with the stdlib engine.
_NAME_TOKEN = r"(?:[A-Za-z][A-Za-z.'\-]{1,30}|[\u4e00-\u9fff]{2,10})"
_CREDENTIAL_KEY = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"access[_-]?key|client[_-]?secret|private[_-]?key)"
)

Rule = namedtuple("Rule", ("name", "pattern", "description", "capture_group"))

SECRET_PATTERNS = (
    # --- Rule sets that already shipped and are kept verbatim for compatibility ---
    Rule(
        "account_id",
        r"(([a-zA-Z]00[1-9][0-9]{5})|((WX|wx)[1-9][0-9]{5})|(00[1-9][0-9]{5})|(3000[0-9]{4})|"
        r"([a-zA-Z](WX|wx)[1-9][0-9]{5})|([a-zA-Z]*kf[1-9][0-9]{4,5})|"
        r"\b([a-zA-Z][1-9][0-9]{4})\b)",
        "Account / customer-service / WeChat IDs",
        0,
    ),
    Rule(
        "url",
        # The XML-namespace exclusion used to be a variable-width lookbehind;
        # it now lives in _URL_CONTEXT_EXCLUDE so the rule also compiles with
        # the stdlib engine.
        r"((?i:ftp://|https?://|www\.)\S+\.\S+)"
        r"|(\w{2,10}\.\w{2,15}\.(?:com|cn|gov|net|edu|mil|biz)(?![0-9a-zA-Z.]))",
        "URLs (excluding XML namespace context)",
        0,
    ),
    Rule(
        "url_ip",
        r"(https?|ftp|file)://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
        r"|(https?|ftp|file)://([-A-Za-z0-9+&@#/%?=~_|!:,.;]+[-A-Za-z0-9+&@#/%=~_|])"
        r"|(?i:ftp://|https?://|www\.)",
        "URLs / IP addresses / protocol prefixes",
        0,
    ),
    Rule(
        "email",
        r"[\w\-\.]{2,100}@(?!openssh)(?:(?:([0-9]{1,3}?\.){3})|(?:(?:[a-zA-Z0-9\-]+\.)+))"
        r"(?!png|so)(?:[a-zA-Z]{2,4})(?![a-zA-Z0-9\-/])",
        "Email addresses",
        0,
    ),
    Rule(
        "account_id_bounded",
        r"\b([a-zA-Z]{0,1}00[1-9]\d{5})($|[^0-9])|\b([a-zA-Z]{0,1}30\d{6})($|[^0-9])"
        r"|([a-zA-Z]{0,1}(WX|wx)[1-9]\d{5,6})($|[^0-9])|([a-zA-Z]*kf[1-9]\d{4,6})($|[^0-9])",
        "Account IDs with word boundaries",
        0,
    ),
    Rule(
        "dts_token",
        r"(DTS|dts)[0-9]{13}",
        "DTS tokens",
        0,
    ),
    # --- Identity and credential coverage added for the disclosure-boundary review ---
    Rule(
        "private_key_or_certificate",
        _PRIVATE_KEY_MARKER + "|" + _CERTIFICATE_MARKER,
        "Private keys and certificates",
        0,
    ),
    Rule(
        "credential_assignment",
        r"(?i)\b" + _CREDENTIAL_KEY + r"\b\s*[:=]\s*[\"']?([^\s\"'<>{}]{8,})[\"']?",
        "Credential-like assignment",
        1,
    ),
    Rule(
        "credential_url",
        r"(?i)\b(?:https?|ftp|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://"
        r"[^\s:@/\"']+:[^\s@/\"']+@[^\s\"']+",
        "Connection string or URL with credentials",
        0,
    ),
    Rule(
        "authorization_header",
        r"(?i)\bauthorization\s*[:=]\s*(?:bearer|basic)\s+[A-Za-z0-9._+/-]{16,}={0,2}",
        "Authorization header with an inline credential",
        0,
    ),
    Rule(
        "internal_ip",
        r"\b(?:10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}|"
        r"192\.168\.[0-9]{1,3}\.[0-9]{1,3}|"
        r"172\.(?:1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3})\b",
        "Private or internal IPv4 address",
        0,
    ),
    Rule(
        "employee_id",
        r"(?i)\b(?:e[0-9]{6,10}|employee[_-]?id\s*[:=]\s*[A-Za-z0-9_-]{4,}|"
        r"工号\s*[:=：]\s*[A-Za-z0-9_-]{4,})\b",
        "Employee identifier",
        0,
    ),
    Rule(
        "numeric_employee_id",
        r"(?i)(?:@?Author\b|作\s*者|工号|employee[_-]?id)[ \t]*[:：][^\n:]{0,30}?"
        r"\b[0-9]{8}\b",
        "Numeric employee ID inside an author header",
        0,
    ),
    Rule(
        "author_personal_name",
        r"(?im)^[ \t]*(?:(?:[*#]+|//+)[ \t]*)?(?:@?Author|作\s*者)[ \t]*[:：][ \t]*"
        + _NAME_TOKEN
        + r"(?:[ \t]+"
        + _NAME_TOKEN
        + r")?(?![:\w/])",
        "Personal name left in an author header",
        0,
    ),
    Rule(
        "nonstandard_id_token",
        # Hex colour tokens such as #ff0000 look identical to this shape, so
        # exclude letter+digit runs made only of hex characters.
        r"\b(?![a-f]{2,5}[0-9]{4,7}\b)[a-z]{2,5}[0-9]{4,7}\b",
        "Non-standard employee ID token that needs manual review",
        0,
    ),
    Rule(
        "personal_path",
        r"(?<![A-Za-z0-9_])/(?:home|Users|workspace)/[A-Za-z0-9._-]{2,}"
        r"(?:/[A-Za-z0-9._-]+)*",
        "Personal or developer path",
        0,
    ),
    Rule(
        "internal_issue_id",
        r"\b(?:DTS|BUG|ISSUE|CR)[-_ ]?[0-9]{5,}\b",
        "Internal defect or issue identifier",
        0,
    ),
)

REVIEW_RULES = {
    "image_manual_review",
    "lfs_pointer",
    "chip_term",
    "author_personal_name",
    "nonstandard_id_token",
}

# Disclosure requirements treat these review-class markers as hard gate
# failures. Keeping them out of REVIEW_RULES preserves their fingerprints while
# allowing main() to promote them into the blocking secret list.
BLOCKING_REVIEW_RULES = {
    "document_metadata",
    "archive_blocked",
    "oversized_file",
}

# Items that cannot be expressed as a reliable rule and always need a human
# pass: non-standard IDs, text rendered inside images, LFS objects and chip
# terms. The report prints this so a clean scan is never mistaken for a
# complete review.
MANUAL_RESIDUAL_NOTE = (
    "non-standard IDs, text inside images (OCR), "
    "LFS objects and chip terms still need manual residual review; "
    "document metadata and files over 1 MiB are blocking"
)

# The ``url`` and ``url_ip`` rules must not fire on XML namespace declarations such as
# xmlns="http://www.w3.org/2000/svg". This used to be expressed as a
# variable-width lookbehind, which the stdlib engine cannot compile, so the
# same condition is applied to the text preceding a match instead.
_URL_CONTEXT_EXCLUDE = re.compile(r'(?:\.|namespace\s{1,4}|(?:xmlns|xsi):?\S{0,30}=".{0,100})$')

PREFERRED_ENGINE_NAME = "regex"
FALLBACK_ENGINE_NAME = "re"

PLACEHOLDER_VALUES = {
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "test",
    "testing",
    "true",
    "false",
    "none",
    "null",
    "your_token",
    "your_password",
}

# Exact internal-environment path exceptions. These values identify documented
# CI image locations, not a developer's personal directory, and are limited to
# a single rule and path by the allowlist.
INTERNAL_PATH_EXCEPTIONS = {
    "/home/jenkins/Ascend",
    "/home/jenkins/Ascend/**",
}


def load_regex_engine():
    """Return ``(engine, engine_name)``.

    ``regex`` is preferred, but every built-in rule is also written to compile
    with the stdlib ``re`` module, so the scanner still runs on a plain
    interpreter. Either way all rules must compile: a rule that fails is a hard
    error, never a silent skip.
    """
    try:
        import regex
    except ImportError:
        return re, FALLBACK_ENGINE_NAME
    return regex, PREFERRED_ENGINE_NAME


def compile_patterns(engine, terms=None):
    """Compile all rules and fail immediately if any rule is invalid."""
    patterns = list(SECRET_PATTERNS)
    if terms:
        escaped_terms = [engine.escape(term) for term in terms]
        chip_pattern = r"(?i)\b(?:" + "|".join(escaped_terms) + r")\b"
        patterns.append(Rule("chip_term", chip_pattern, "Approved chip-term review list", 0))

    compiled = []
    for rule in patterns:
        try:
            rx = engine.compile(rule.pattern)
        except Exception as exc:
            raise ConfigError("rule '{}' failed to compile: {}".format(rule.name, exc))
        compiled.append((rule.name, rx, rule.description, rule.capture_group))
    if not compiled:
        raise ConfigError("no scanner rules configured")
    return compiled


def _is_placeholder(value):
    normalized = value.strip("\"'").strip().lower()
    if not normalized or len(normalized) < 8:
        return True
    if any(marker in normalized for marker in ("${", "$(", "<", ">", "{{", "env:")):
        return True
    if set(normalized) <= set("x*"):
        return True
    return normalized in PLACEHOLDER_VALUES


def _is_documented_environment_path(value):
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in INTERNAL_PATH_EXCEPTIONS)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

ALLOWLIST_KEYS = {"rule", "path", "value", "value_regex", "expires"}
REQUIRED_ALLOWLIST_KEYS = {"rule", "path"}
LEGACY_REGEX_PREFIX = "regex:"


def _parse_allowlist_line(line, line_no):
    """Parse one allowlist line into a scoped entry or a legacy entry.

    Scoped entries start with a known ``key=value`` field. Anything else keeps
    the legacy semantics used by the committed allowlist file: a plain
    substring, or ``regex:PATTERN`` compiled with the stdlib engine.
    """
    stripped = line.strip()
    if stripped.startswith(LEGACY_REGEX_PREFIX):
        pattern = stripped[len(LEGACY_REGEX_PREFIX) :]
        if not pattern:
            raise ConfigError("allowlist line {} has an empty regex".format(line_no))
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ConfigError("allowlist line {} has an invalid regex: {}".format(line_no, exc))
        return {"legacy_regex": pattern, "compiled_legacy_regex": compiled}

    try:
        parts = shlex.split(line, comments=True, posix=True)
    except ValueError as exc:
        raise ConfigError("allowlist line {} is invalid: {}".format(line_no, exc))
    if not parts:
        return None

    first_key = parts[0].split("=", 1)[0] if "=" in parts[0] else ""
    if first_key not in ALLOWLIST_KEYS:
        # Legacy literal substring entry.
        return {"legacy_literal": stripped}

    entry = {}
    for part in parts:
        if "=" not in part:
            raise ConfigError("allowlist line {} must contain key=value fields".format(line_no))
        key, value = part.split("=", 1)
        if key not in ALLOWLIST_KEYS:
            raise ConfigError("allowlist line {} has unknown key '{}'".format(line_no, key))
        if not value:
            raise ConfigError("allowlist line {} has empty value for '{}'".format(line_no, key))
        entry[key] = value
    missing = REQUIRED_ALLOWLIST_KEYS - set(entry)
    if missing:
        raise ConfigError(
            "allowlist line {} is missing required fields: {}".format(line_no, ", ".join(sorted(missing)))
        )
    if ("value" in entry) == ("value_regex" in entry):
        raise ConfigError("allowlist line {} must specify exactly one of value or value_regex".format(line_no))
    return entry


def load_allowlist(source, engine):
    """Load scoped allowlist entries plus the legacy committable format."""
    if not source:
        return []
    try:
        lines = source.readlines()
    except AttributeError:
        try:
            with open(source, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeError) as exc:
            raise ConfigError("cannot read allowlist '{}': {}".format(source, exc))

    entries = []
    for line_no, raw_line in enumerate(lines, 1):
        entry = _parse_allowlist_line(raw_line, line_no)
        if entry is None:
            continue
        if "legacy_regex" in entry or "legacy_literal" in entry:
            entries.append(entry)
            continue
        entry["path"] = entry["path"].replace(os.sep, "/")
        if entry["path"].startswith("./"):
            entry["path"] = entry["path"][2:]
        if "value_regex" in entry:
            pattern = entry["value_regex"]
            if not (pattern.startswith("^") and pattern.endswith("$")):
                raise ConfigError("allowlist line {} value_regex must be anchored with ^ and $".format(line_no))
            try:
                entry["compiled_value_regex"] = engine.compile(pattern)
            except Exception as exc:
                raise ConfigError("allowlist line {} has invalid value_regex: {}".format(line_no, exc))
        if "expires" in entry:
            try:
                expires = datetime.strptime(entry["expires"], "%Y-%m-%d").date()
            except ValueError:
                raise ConfigError("allowlist line {} has invalid expires date".format(line_no)) from None
            if expires < datetime.now(timezone.utc).date():
                raise ConfigError("allowlist line {} has expired".format(line_no))
        entries.append(entry)
    return entries


def allowlist_legacy_count(allowlist):
    """Return how many loaded entries use the deprecated broad format."""
    return sum(1 for entry in allowlist if "legacy_literal" in entry or "legacy_regex" in entry)


def _legacy_match_text(finding):
    """Rebuild the historical match string so legacy entries keep working."""
    snippet = (finding.line_text or "").strip()[:120]
    return "{}:{}:{}  [{}]  {}  | {}".format(
        finding.file, finding.line_no, finding.col, finding.rule, finding.match, snippet
    )


def is_allowed(finding, allowlist):
    """Return True when a scoped approval or a legacy entry suppresses *finding*."""
    legacy_text = None
    for entry in allowlist:
        if "legacy_literal" in entry or "legacy_regex" in entry:
            if legacy_text is None:
                legacy_text = _legacy_match_text(finding)
            if "legacy_literal" in entry:
                if entry["legacy_literal"] in finding.match or entry["legacy_literal"] in legacy_text:
                    return True
            elif entry["compiled_legacy_regex"].search(legacy_text) or entry["compiled_legacy_regex"].search(
                finding.match
            ):
                return True
            continue
        if finding.rule != entry["rule"] or finding.file != entry["path"]:
            continue
        if "value" in entry:
            if finding.match == entry["value"]:
                return True
        elif entry["compiled_value_regex"].fullmatch(finding.match):
            return True
    return False


# ---------------------------------------------------------------------------
# Terms
# ---------------------------------------------------------------------------


def load_chip_terms(source):
    """Load exact chip terms for manual review classification."""
    if not source:
        return []
    try:
        lines = source.readlines()
    except AttributeError:
        try:
            with open(source, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeError) as exc:
            raise ConfigError("cannot read terms file '{}': {}".format(source, exc))
    terms = [line.strip() for line in lines]
    terms = [term for term in terms if term and not term.startswith("#")]
    if not terms:
        raise ConfigError("terms input contains no terms")
    if any(len(term) < 3 for term in terms):
        raise ConfigError("chip terms must be at least three characters long")
    if len(set(term.lower() for term in terms)) != len(terms):
        raise ConfigError("chip terms input contains duplicate terms")
    return terms


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------

MAX_TEXT_FILE_SIZE = 50 * 1024 * 1024
MAX_BINARY_FILE_SIZE = 50 * 1024 * 1024

# Only used for paths that are not inside a git repository, where `git ls-files`
# cannot tell us what is publishable.
SKIP_DIRS = {
    ".git",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

IMAGE_EXTENSIONS = {".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
ARCHIVE_EXTENSIONS = {".zip", ".tar", ".tgz", ".tbz2", ".txz"}
COMPRESSED_EXTENSIONS = {".gz", ".bz2", ".xz"}
DOCUMENT_EXTENSIONS = {".docx", ".xlsx", ".pptx"}
TEXT_CHUNK_OVERLAP = 4096

# Archive-like and document-like files are also blocked by the dedicated size
# gate below; this limit only prevents pathological inputs from consuming too
# much memory during metadata extraction.
MAX_GATE_FILE_SIZE = 1024 * 1024

# The scanner keeps its own rule literals here, and the allowlist is data rather
# than publishable content. Scanning them would only produce self-matches, which
# the previous implementation avoided the same way. The test file intentionally
# contains secret-like fixtures to verify rule behaviour.
SKIP_BASENAMES = {"secret_scan.py", "secret_scan_allowlist.txt", "test_secret_scan.py"}


def _git_output(args, cwd):
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ConfigError("cannot execute git: {}".format(exc))
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace").strip()
        raise ConfigError("git command failed: {}".format(stderr or result.returncode))
    return result.stdout


def _find_repo_root(path):
    """Return the git top level for *path*, or None when it is not in a repo."""
    try:
        output = _git_output(["rev-parse", "--show-toplevel"], cwd=path)
    except ConfigError:
        return None
    return os.path.abspath(output.decode("utf-8", "replace").strip())


def _repo_root(path):
    """Return the root used for display paths and allowlist lookup.

    A single file argument must resolve against its directory, otherwise the
    finding path collapses to "." in the report.
    """
    base = path if os.path.isdir(path) else (os.path.dirname(path) or os.curdir)
    return _find_repo_root(base) or os.path.abspath(base)


def _walk_files(root):
    """Yield every regular file under *root* for paths outside a git repository."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            if name in SKIP_BASENAMES:
                continue
            path = os.path.join(dirpath, name)
            if os.path.isfile(path):
                yield path


def iter_scan_files(roots, include_untracked=False):
    """Yield tracked files selected by *roots* (plus opt-in untracked files).

    Paths outside a git repository fall back to a filesystem walk so the
    scanner keeps working on plain directories, as it did before it started
    relying on ``git ls-files`` for full file-type coverage.
    """
    seen = set()
    for root in roots:
        absolute_root = os.path.abspath(root)
        if os.path.isfile(absolute_root):
            if os.path.basename(absolute_root) not in SKIP_BASENAMES and absolute_root not in seen:
                seen.add(absolute_root)
                yield absolute_root
            continue
        if not os.path.isdir(absolute_root):
            raise ConfigError("scan path does not exist: {}".format(root))

        repo_root = _find_repo_root(absolute_root)
        if repo_root is None:
            for path in _walk_files(absolute_root):
                if path not in seen:
                    seen.add(path)
                    yield path
            continue
        if os.path.commonpath([repo_root, absolute_root]) != repo_root:
            raise ConfigError("scan path is outside its git repository: {}".format(root))
        try:
            relative_root = os.path.relpath(absolute_root, repo_root)
        except ValueError as exc:
            raise ConfigError("cannot make scan path relative: {}".format(exc))
        if relative_root == ".":
            path_spec = []
        else:
            path_spec = [relative_root.replace(os.sep, "/")]

        command = ["ls-files", "-z", "--cached"]
        if include_untracked:
            command.extend(["--others", "--exclude-standard"])
        command.extend(["--"] + path_spec)
        output = _git_output(command, cwd=repo_root)
        for raw_path in output.split(b"\0"):
            if not raw_path:
                continue
            relative_path = raw_path.decode("utf-8", "surrogateescape")
            absolute_path = os.path.join(repo_root, relative_path)
            if os.path.basename(relative_path) in SKIP_BASENAMES or absolute_path in seen:
                continue
            if absolute_path not in seen:
                seen.add(absolute_path)
                yield absolute_path


def _relative_path(path, repo_root):
    try:
        relative = os.path.relpath(path, repo_root)
    except ValueError:
        return path.replace(os.sep, "/")
    if relative.startswith(".."):
        return path.replace(os.sep, "/")
    return relative.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Findings and text scanning
# ---------------------------------------------------------------------------


class Finding(object):
    __slots__ = ("file", "line_no", "col", "rule", "match", "line_text")

    def __init__(self, file, line_no, col, rule, match, line_text=""):
        self.file = file
        self.line_no = line_no
        self.col = col
        self.rule = rule
        self.match = match
        self.line_text = line_text

    @property
    def severity(self):
        return "review" if self.rule in REVIEW_RULES else "secret"

    @property
    def key(self):
        # Same location and text is one finding, even when several rules overlap.
        return (self.file, self.line_no, self.col, self.match)

    @property
    def fingerprint(self):
        value = "\0".join([self.file, str(self.line_no), str(self.col), self.rule, self.match]).encode(
            "utf-8", "surrogatepass"
        )
        return hashlib.sha256(value).hexdigest()[:12]

    def display(self, verbose=False):
        base = "{}:{}:{} [{}] severity={} fingerprint={}".format(
            self.file, self.line_no, self.col, self.rule, self.severity, self.fingerprint
        )
        if verbose:
            return base + " match={!r} context={!r}".format(self.match, self.line_text[:160])
        return base


def _match_is_actionable(rule_name, match_object, capture_group):
    if rule_name == "personal_path":
        return not _is_documented_environment_path(match_object.group(0))
    if rule_name in {"url", "url_ip"}:
        return not _URL_CONTEXT_EXCLUDE.search(match_object.string[: match_object.start()])
    if rule_name == "credential_assignment":
        try:
            value = match_object.group(capture_group)
        except IndexError:
            return False
        return value is not None and not _is_placeholder(value)
    return True


def scan_text(text, compiled_patterns, file, line_no=1, line_text=None):
    """Scan one text chunk and yield findings."""
    if line_text is None:
        line_text = text
    for rule_name, rx, _description, capture_group in compiled_patterns:
        for match_object in rx.finditer(text):
            if not _match_is_actionable(rule_name, match_object, capture_group):
                continue
            matched_text = match_object.group(0)
            if not matched_text or matched_text.isspace():
                continue
            yield Finding(file, line_no, match_object.start() + 1, rule_name, matched_text, line_text)


def scan_line_stream(handle, compiled_patterns, file, max_bytes):
    """Stream and scan text without loading an entire large file."""
    total = 0
    for line_no, raw_line in enumerate(handle, 1):
        total += len(raw_line)
        if total > max_bytes:
            raise ExecutionError("text input exceeds safe scan limit")
        try:
            line = raw_line.decode("utf-8", "replace")
        except AttributeError:
            line = raw_line
        for finding in scan_text(line, compiled_patterns, file, line_no, line.rstrip("\r\n")):
            yield finding


def _is_binary_file(path, sample_size=8192):
    with open(path, "rb") as handle:
        chunk = handle.read(sample_size)
    return b"\x00" in chunk


def _is_lfs_pointer(path):
    try:
        with open(path, "rb") as handle:
            prefix = handle.read(64)
    except OSError as exc:
        raise ExecutionError("cannot read file: {}".format(exc))
    return prefix.startswith(b"version https://git-lfs.github.com/spec/v1\n")


def _open_text(path):
    return open(path, "r", encoding="utf-8", errors="replace")


def _scan_binary_strings(path, compiled_patterns, file):
    """Extract printable strings from binary or embedded content safely."""
    remaining = b""
    offset = 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            data = remaining + chunk
            text = data.decode("latin-1", "ignore")
            for finding in scan_text(text, compiled_patterns, file, 1, ""):
                yield Finding(
                    finding.file,
                    finding.line_no,
                    finding.col + offset,
                    finding.rule,
                    finding.match,
                    "",
                )
            remaining = data[-TEXT_CHUNK_OVERLAP:]
            offset += len(data) - len(remaining)


# ---------------------------------------------------------------------------
# Images, LFS, archives
# ---------------------------------------------------------------------------


def _metadata_texts(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str) and value and len(value) <= 4096:
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _document_metadata_texts(path):
    """Extract the metadata fields relevant to the disclosure checklist.

    OOXML documents keep authorship, revisions, names and absolute paths in
    ``docProps/core.xml``/``app.xml``. Reading these two small members is
    dependency-free and covers DOCX, XLSX and PPTX. Corrupt or non-standard
    documents remain scan failures instead of silently passing the gate.
    """
    text_values = []
    with zipfile.ZipFile(path) as archive:
        for member_name in ("docProps/core.xml", "docProps/app.xml"):
            try:
                raw_xml = archive.read(member_name)
            except KeyError:
                continue
            try:
                root = ElementTree.fromstring(raw_xml)
            except ElementTree.ParseError as exc:
                raise ExecutionError("cannot parse document metadata: {}".format(exc))
            for element in root.iter():
                text = element.text
                if text:
                    text = text.strip()
                if text:
                    text_values.append("{}={}".format(element.tag.rsplit("}", 1)[-1], text))
    return text_values


def scan_document_metadata(path, compiled_patterns, file):
    """Check document metadata and emit a blocking metadata marker."""
    for text in _document_metadata_texts(path):
        for finding in scan_text(text, compiled_patterns, file, 1, ""):
            yield finding
    yield Finding(file, 1, 1, "document_metadata", "[document metadata requires removal or approval]", "")


def scan_image(path, compiled_patterns, file):
    """Inspect image metadata and emit an OCR/manual-review marker."""
    try:
        from PIL import Image
    except ImportError:
        # Metadata cannot be read without Pillow, but the image itself still
        # needs review: report it instead of silently skipping the file.
        yield Finding(
            file,
            1,
            1,
            "image_manual_review",
            "[image metadata scan unavailable; manual review required]",
            "",
        )
        return

    try:
        with Image.open(path) as image:
            metadata_values = []
            for value in image.info.values():
                text = _metadata_texts(value)
                if text:
                    metadata_values.append(text)
            try:
                exif = image.getexif()
            except Exception:
                exif = {}
            for value in exif.values():
                text = _metadata_texts(value)
                if text:
                    metadata_values.append(text)
    except ConfigError:
        raise
    except Exception as exc:
        raise ExecutionError("cannot inspect image metadata: {}".format(exc))

    for text in metadata_values:
        for finding in scan_text(text, compiled_patterns, file, 1, ""):
            yield finding
    yield Finding(file, 1, 1, "image_manual_review", "[image content requires OCR review]", "")


def scan_file(filepath, compiled_patterns, repo_root=""):
    """Scan a file, image, archive, document, LFS pointer, or embedded strings."""
    display_path = _relative_path(filepath, repo_root) if repo_root else filepath
    try:
        mode = os.stat(filepath).st_mode
    except OSError as exc:
        raise ExecutionError("cannot stat file: {}".format(exc))
    if not stat.S_ISREG(mode):
        return

    if _is_lfs_pointer(filepath):
        yield Finding(display_path, 1, 1, "lfs_pointer", "[LFS object requires review]", "")
        return

    extension = os.path.splitext(display_path)[1].lower()
    file_size = os.path.getsize(filepath)
    if file_size > MAX_GATE_FILE_SIZE:
        yield Finding(display_path, 1, 1, "oversized_file", "[file exceeds the 1 MiB publishable limit]", "")
        return

    if extension in IMAGE_EXTENSIONS:
        yield from _scan_image_file(filepath, compiled_patterns, display_path, file_size)
        return
    if extension in DOCUMENT_EXTENSIONS:
        yield from _scan_document_file(filepath, compiled_patterns, display_path, file_size)
        return
    if extension in ARCHIVE_EXTENSIONS or display_path.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        yield Finding(display_path, 1, 1, "archive_blocked", "[archives require removal or scoped approval]", "")
        return

    if file_size > MAX_TEXT_FILE_SIZE:
        raise ExecutionError("file exceeds safe text scan limit")
    if _is_binary_file(filepath):
        if file_size > MAX_BINARY_FILE_SIZE:
            raise ExecutionError("binary file exceeds safe scan limit")
        for finding in _scan_binary_strings(filepath, compiled_patterns, display_path):
            yield finding
        return

    with _open_text(filepath) as handle:
        for finding in scan_line_stream(handle, compiled_patterns, display_path, MAX_TEXT_FILE_SIZE):
            yield finding


def _scan_image_file(filepath, compiled_patterns, display_path, file_size):
    """Scan an image while enforcing its binary-size safety limit."""
    if file_size > MAX_BINARY_FILE_SIZE:
        raise ExecutionError("image exceeds safe scan limit")
    for finding in scan_image(filepath, compiled_patterns, display_path):
        yield finding


def _scan_document_file(filepath, compiled_patterns, display_path, file_size):
    """Scan document metadata while enforcing its binary-size safety limit."""
    if file_size > MAX_BINARY_FILE_SIZE:
        raise ExecutionError("document exceeds safe scan limit")
    for finding in scan_document_metadata(filepath, compiled_patterns, display_path):
        yield finding


def deduplicate(findings):
    seen = set()
    result = []
    for finding in findings:
        if finding.key not in seen:
            seen.add(finding.key)
            result.append(finding)
    return result


# ---------------------------------------------------------------------------
# Reporting and CLI
# ---------------------------------------------------------------------------


def print_report(
    findings,
    review_findings,
    suppressed,
    pattern_count,
    engine_name,
    scan_paths,
    allowlist_count,
    allowlist_legacy,
    file_count,
    verbose=False,
):
    print()
    print("=" * 76)
    print("  SECURITY SCAN REPORT")
    print("=" * 76)
    print("  Engine:    {}".format(engine_name))
    print("  Rules:     {} compiled, 0 skipped".format(pattern_count))
    print("  Files:     {} scanned".format(file_count))
    print("  Allowlist: {} entries ({} legacy, broad; prefer scoped entries)".format(allowlist_count, allowlist_legacy))
    print("  Residual:  {}".format(MANUAL_RESIDUAL_NOTE))
    print("  Paths:     {}".format(", ".join(scan_paths)))
    print("=" * 76)

    if findings:
        print()
        print("  Secret findings ({}):".format(len(findings)))
        for finding in findings:
            print("  " + finding.display(verbose))
    if review_findings:
        print()
        print("  Manual-review findings ({}):".format(len(review_findings)))
        for finding in review_findings:
            print("  " + finding.display(verbose))
    if suppressed:
        print()
        print("  Suppressed by allowlist: {}".format(len(suppressed)))
    if not findings and not review_findings and not suppressed:
        print()
        print("  [OK] No potential secrets or review markers found.")
    print()
    if findings:
        print("  Result: FAILED — secret findings require remediation or scoped approval")
    elif review_findings:
        print("  Result: PASSED with manual-review items (use --strict-manual-review to block)")
    else:
        print("  Result: PASSED")
    print()


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="secret_scan.py",
        description="Scan publishable repository content with redacted public output.",
    )
    parser.add_argument("paths", nargs="*", default=["."], help="Paths to scan")
    parser.add_argument("--allowlist", default=None, help="Scoped allowlist file")
    parser.add_argument("--terms", default=None, help="Exact chip terms requiring manual review")
    parser.add_argument("--include-untracked", action="store_true", help="Include non-ignored untracked files")
    parser.add_argument("--strict-manual-review", action="store_true", help="Fail on review-class findings")
    parser.add_argument("--verbose", action="store_true", help="Authorized local mode; prints raw matches")
    parser.add_argument("--exit-zero", action="store_true", help="Return zero after scanning")
    parser.add_argument("--no-allowlist", action="store_true", help="Disable allowlist")
    return parser.parse_args(argv)


def _default_allowlist_path(repo_root, script_dir):
    """Return the first existing default allowlist path."""
    candidates = [
        os.path.join(repo_root, ".gitcode", "scripts", "secret_scan_allowlist.txt"),
        os.path.join(script_dir, "secret_scan_allowlist.txt"),
    ]
    return next((path for path in candidates if os.path.isfile(path)), None)


def _resolve_allowlist(args, repo_root, engine):
    """Resolve and load the configured allowlist."""
    if args.no_allowlist:
        return []
    allowlist_path = args.allowlist
    if not allowlist_path:
        allowlist_path = _default_allowlist_path(repo_root, os.path.dirname(os.path.abspath(__file__)))
    return load_allowlist(allowlist_path, engine)


def _collect_findings(files, compiled_patterns, repo_root):
    """Scan files and return deduplicated findings."""
    findings = []
    for filepath in files:
        findings.extend(scan_file(filepath, compiled_patterns, repo_root))
    return deduplicate(findings)


def _classify_findings(findings, allowlist):
    """Split findings into blocking, review, and suppressed lists."""
    reported = []
    review = []
    suppressed = []
    for finding in findings:
        if is_allowed(finding, allowlist):
            suppressed.append(finding)
        elif finding.severity == "review":
            destination = reported if finding.rule in BLOCKING_REVIEW_RULES else review
            destination.append(finding)
        else:
            reported.append(finding)
    return reported, review, suppressed


def _print_scan_progress(engine_name, compiled_patterns, display_paths, file_count):
    """Print scan setup details before the final report."""
    print("[secret-scan] Engine: {}".format(engine_name))
    if engine_name == FALLBACK_ENGINE_NAME:
        print(
            "[secret-scan] NOTE: the 'regex' package is not installed, so the stdlib 're' "
            "engine is used. All {} rules compiled, none skipped; install 'regex' to match "
            "the CI engine exactly.".format(len(compiled_patterns))
        )
    print("[secret-scan] Rules: {} compiled, 0 skipped".format(len(compiled_patterns)))
    print("[secret-scan] Scanning: {}".format(", ".join(display_paths)))
    print("[secret-scan] Files to scan: {}".format(file_count))


def _scan_exit_code(args, reported, review):
    """Return the command exit code implied by classified findings."""
    if args.exit_zero:
        return 0
    if reported:
        return 1
    if review and args.strict_manual_review:
        return 1
    return 0


def _resolve_scan_roots(roots):
    """Accept the old CI package path without skipping missing-path checks."""
    source_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    legacy_root = os.path.join(source_root, "cannsim")
    current_root = os.path.join(source_root, "npusim")
    resolved = []
    for root in roots:
        if os.path.abspath(root) == legacy_root and not os.path.lexists(legacy_root) and os.path.isdir(current_root):
            print("[secret-scan] Compatibility: cannsim -> npusim")
            root = current_root
        resolved.append(root)
    return resolved


def main(argv=None):
    args = _parse_args(argv)
    try:
        engine, engine_name = load_regex_engine()
        chip_terms = load_chip_terms(args.terms)
        compiled_patterns = compile_patterns(engine, chip_terms)

        roots = _resolve_scan_roots(args.paths or ["."])
        repo_root = _repo_root(os.path.abspath(roots[0]))
        allowlist = _resolve_allowlist(args, repo_root, engine)

        files = list(iter_scan_files(roots, include_untracked=args.include_untracked))
        findings = _collect_findings(files, compiled_patterns, repo_root)
        reported, review, suppressed = _classify_findings(findings, allowlist)

        display_paths = [_relative_path(os.path.abspath(path), repo_root) for path in roots]
        _print_scan_progress(engine_name, compiled_patterns, display_paths, len(files))
        print_report(
            reported,
            review,
            suppressed,
            len(compiled_patterns),
            engine_name,
            display_paths,
            len(allowlist),
            allowlist_legacy_count(allowlist),
            len(files),
            verbose=args.verbose,
        )

        return _scan_exit_code(args, reported, review)
    except (ConfigError, ExecutionError) as exc:
        print("[secret-scan] ERROR: {}".format(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
