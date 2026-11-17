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

"""Best-effort device ELF archival and unambiguous per-kernel selection."""

import os
import re
import signal
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

from npusim.core.public.comm import print_info, print_warn

EXTRACT_TIMEOUT = 120
_SYMBOL = re.compile(
    r"^\s*(?P<addr>[0-9a-fA-F]+)\s+g\s+F\s+\.text(?:\.[^\s]+)?"
    r"\s+(?P<size>[0-9a-fA-F]+)\s+(?P<name>.+?)\s*$"
)
_INSTRUCTION = re.compile(r"^\s*([0-9a-fA-F]+):\s+([0-9a-fA-F]{8,32})\s+\S.*$")


class DeviceObject(NamedTuple):
    path: str
    kernel: str
    entry_pc: int = 0

    def elf_pc(self, record_pc: int) -> int:
        return record_pc + self.entry_pc

    def record_pc(self, elf_pc: int) -> int:
        return elf_pc - self.entry_pc


def _tools():
    # Reuse the CANN installation/environment lookup used by source mapping.
    from npusim.core.report.detections.pc_source_mapper import _find_tool, _tool_env

    return _find_tool, _tool_env


def _run_tool(argv, timeout, env):
    """Bound the whole tool process group, including msobjdump's shell wrapper."""
    with subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def is_device_elf(path: Path) -> bool:
    """Validate a complete ELF64 little-endian Ascend object, not a host ELF.

    Check every file-backed section/program extent before accepting extraction
    output. A magic-only/truncated file must never enable object analyzers.
    """
    try:
        if path.is_symlink() or not path.is_file():
            return False
        size = path.stat().st_size
        with path.open("rb") as stream:
            header = stream.read(64)
            if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
                return False
            fields = struct.unpack("<HHIQQQIHHHHHH", header[16:])
            kind, machine, version, _, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, shstr = fields
            if machine != 0x1029 or kind not in (1, 2, 3) or version != 1 or ehsize != 64:
                return False
            if not shnum or shsize != 64 or shstr >= shnum or shoff < 64 or shoff + shnum * shsize > size:
                return False
            if phnum and (phsize != 56 or phoff < 64 or phoff + phnum * phsize > size):
                return False
            has_code = False
            for index in range(shnum):
                stream.seek(shoff + index * shsize)
                _, stype, flags, _, offset, length, _, _, _, _ = struct.unpack("<IIQQQQIIQQ", stream.read(64))
                if stype != 8 and offset + length > size:  # SHT_NOBITS has no file payload
                    return False
                has_code = has_code or (stype == 1 and bool(flags & 4) and length > 0)
            for index in range(phnum):
                stream.seek(phoff + index * phsize)
                _, _, offset, _, _, length, _, _ = struct.unpack("<IIQQQQQQ", stream.read(56))
                if offset + length > size:
                    return False
            return has_code
    except (OSError, ValueError, struct.error):
        return False


def extract_device_objects(app: Path, destination: Path) -> bool:
    """Extract privately; publish only complete objects, never overwrite a path."""
    find_tool, tool_env = _tools()
    tool = find_tool("msobjdump", "tools/msobjdump/msobjdump")
    if not tool:
        print_warn("AI Core object extraction skipped: msobjdump not found")
        return False
    try:
        if destination.exists() or destination.is_symlink() or destination.parent.is_symlink():
            print_warn("AI Core object extraction skipped: destination already exists or is a symlink")
            return False
        with tempfile.TemporaryDirectory(prefix=".aicore-extract-", dir=str(destination.parent)) as tmp:
            stage = Path(tmp)
            result = _run_tool(
                [tool, "--extract-elf", str(app.resolve()), "--out-dir", str(stage)],
                timeout=EXTRACT_TIMEOUT,
                env=tool_env(),
            )
            if result.returncode:
                detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:240]
                print_warn(f"AI Core object extraction failed (rc={result.returncode}): {detail}")
                return False
            files = sorted(stage.iterdir())
            objects = [p for p in files if is_device_elf(p)]
            if not objects or any(p.is_symlink() or (p.suffix in (".o", ".elf") and p not in objects) for p in files):
                print_warn("AI Core object extraction produced no valid complete device ELF set")
                return False
            destination.mkdir()  # exclusive: existing data is never replaced
            incomplete = destination / ".incomplete"
            incomplete.touch(exist_ok=False)
            for index, obj in enumerate(objects):
                # Do not propagate tool-controlled filenames into the archive.
                with obj.open("rb") as src, (destination / f"device_{index:04d}.o").open("xb") as dst:
                    shutil.copyfileobj(src, dst)
            incomplete.unlink()
            print_info(f"Archived {len(objects)} AI Core object(s) under {destination}")
            return True
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print_warn(f"AI Core object extraction skipped; continuing without objects: {exc}")
        return False


def archive_candidates(export: Path) -> List[Path]:
    result = []
    for base in (export, export / "record"):
        root = base / "aicore_objects"
        if (
            root.is_symlink()
            or not root.is_dir()
            or (root / ".incomplete").exists()
            or (root / ".incomplete").is_symlink()
        ):
            continue
        try:
            for path in sorted(root.iterdir()):
                if is_device_elf(path) and path.resolve() not in result:
                    result.append(path.resolve())
        except OSError as exc:
            print_warn(f"AI Core object archive unreadable: {exc}")
    return result


def _kernel_base(name: str) -> str:
    for suffix in ("_mix_aic", "_mix_aiv"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def object_symbols(path: Path) -> Dict[str, Set[str]]:
    """Index exact raw and demangled function identities (never substrings).

    Retain overloads as separate raw symbols: an unqualified overloaded name
    is ambiguous even when both definitions live in the same ELF.
    """
    find_tool, tool_env = _tools()
    tool = find_tool("llvm-objdump", "tools/bisheng_compiler/bin/llvm-objdump", "aarch64-linux/bin/llvm-objdump")
    if not tool:
        print_warn("AI Core object matching skipped: llvm-objdump not found")
        return {}
    try:
        outputs = []
        for flags in ([], ["--demangle"]):
            res = _run_tool([tool, "-t"] + flags + [str(path)], timeout=30, env=tool_env())
            if res.returncode:
                return {}
            outputs.append([m["name"] for line in res.stdout.splitlines() for m in [_SYMBOL.fullmatch(line)] if m])
        if len(outputs[0]) != len(outputs[1]):
            return {}
        result: Dict[str, Set[str]] = {}
        for raw, demangled in zip(*outputs):
            symbol = _kernel_base(raw)
            aliases = {raw, symbol, demangled, _kernel_base(demangled.split("(", 1)[0])}
            for alias in aliases:
                result.setdefault(alias, set()).add(symbol)
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def object_code(path: Path) -> Tuple[List[Tuple[int, int, str]], Dict[int, int]]:
    """Read global entry ranges and instruction encodings with the CANN decoder."""
    find_tool, tool_env = _tools()
    tool = find_tool("llvm-objdump", "tools/bisheng_compiler/bin/llvm-objdump", "aarch64-linux/bin/llvm-objdump")
    if not tool:
        return [], {}
    try:
        symbols = _run_tool([tool, "-t", str(path)], timeout=30, env=tool_env())
        code = _run_tool([tool, "-d", str(path)], timeout=30, env=tool_env())
        if symbols.returncode or code.returncode:
            return [], {}
        entries = [
            (int(m["addr"], 16), int(m["size"], 16), _kernel_base(m["name"]))
            for line in symbols.stdout.splitlines()
            for m in [_SYMBOL.fullmatch(line)]
            if m
        ]
        instructions = {}
        for line in code.stdout.splitlines():
            match = _INSTRUCTION.fullmatch(line)
            if match:
                pc, encoding = (int(value, 16) for value in match.groups())
                # Section-relative addresses must identify exactly one instruction.
                if pc in instructions:
                    return [], {}
                instructions[pc] = encoding
        return entries, instructions
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [], {}


def _trace_encodings(rows: Sequence[Tuple[int, str]]) -> Dict[int, int]:
    instructions = {}
    for pc, text in rows:
        if not isinstance(pc, int) or pc < 0 or not isinstance(text, str):
            raise ValueError("invalid recorded instruction")
        encoding = text[2:] if text.startswith("0x") else text
        if len(encoding) not in (8, 16, 32) or not re.fullmatch(r"[0-9a-fA-F]+", encoding):
            raise ValueError("missing or invalid recorded encoding")
        # Helper prints 128-bit SIMT instructions as low uint64 then high uint64;
        # objdump prints the whole integer. Padded 32-bit values compare as integers.
        if len(encoding) == 32:
            encoding = encoding[16:] + encoding[:16]
        value = int(encoding, 16)
        if pc in instructions and instructions[pc] != value:
            raise ValueError("conflicting encodings at one recorded PC")
        instructions[pc] = value
    return instructions


class DeviceObjectResolver:
    """Resolve one launch by exact symbol or an unambiguous instruction trace."""

    def __init__(self, export: Path, explicit: Optional[str] = None):
        if export.name == "cannsim_helper_dbs" or re.fullmatch(r"chip\d+", export.name):
            export = export.parent
        self.export = export
        self.explicit = explicit
        self._temporary = None
        self._indices = None
        self._code = {}

    def _candidates(self) -> List[Path]:
        if self.explicit:
            source = Path(self.explicit)
        else:
            archived = archive_candidates(self.export)
            # An existing archive is authoritative: never fall back on mismatch.
            if archived or any(
                (base / "aicore_objects").exists() or (base / "aicore_objects").is_symlink()
                for base in (self.export, self.export / "record")
            ):
                return archived
            source = None
            for base in (self.export, self.export / "record"):
                try:
                    text = (base / ".user-app").read_text(encoding="utf-8").strip()
                    if text and Path(text).is_file():
                        source = Path(text)
                        break
                except (OSError, ValueError):
                    continue
            if source is None:
                return []
        if is_device_elf(source):
            return [source.resolve()]
        # Host ARM/x86 ELFs are containers, never ObjectProcessor input.
        try:
            with source.open("rb") as stream:
                if stream.read(4) != b"\x7fELF":
                    return []
        except (OSError, ValueError):
            return []
        self._temporary = tempfile.TemporaryDirectory(prefix="npusim-objects-")
        destination = Path(self._temporary.name) / "aicore_objects"
        if extract_device_objects(source, destination):
            return archive_candidates(Path(self._temporary.name))
        return []

    def _match_trace(self, paths: Sequence[Path], rows: Sequence[Tuple[int, str]]) -> List[DeviceObject]:
        try:
            trace = _trace_encodings(rows)
        except ValueError:
            return []
        if not trace:
            return []
        matches = []
        for path in paths:
            if path not in self._code:
                self._code[path] = object_code(path)
            entries, instructions = self._code[path]
            for kernel, entry_pc in sorted({(name, addr) for addr, _, name in entries}):
                candidate = DeviceObject(str(path), kernel, entry_pc)
                if any(instructions.get(candidate.elf_pc(pc)) != encoding for pc, encoding in trace.items()):
                    continue
                kernels = {
                    name
                    for addr, size, name in entries
                    if any(addr <= candidate.elf_pc(pc) < addr + size for pc in trace)
                }
                if kernels == {kernel}:
                    matches.append(candidate)
        return matches

    def select(
        self, kernel: str, executed_instructions: Optional[Sequence[Tuple[int, str]]] = None
    ) -> Optional[DeviceObject]:
        if self._indices is None:
            self._indices = [(p, object_symbols(p)) for p in self._candidates()]
        named = {(str(path), symbol) for path, names in self._indices for symbol in names.get(kernel, set())}
        matches = [DeviceObject(path, symbol) for path, symbol in sorted(named)]
        if executed_instructions is not None:
            # Names from runtime/debug tracking can be an inlined helper name.
            # Verify every recorded PC/code pair and require a single entry group
            # across all objects; never choose the sole object just by its count.
            verified = self._match_trace([p for p, _ in self._indices], executed_instructions)
            matches = [m for m in verified if not named or (m.path, m.kernel) in named]
        if len(matches) == 1:
            if executed_instructions is not None:
                selected = matches[0]
                print_info(
                    f"AI Core object resolved for {kernel!r}: {selected.path} "
                    f"(entry {selected.kernel}, ELF PC 0x{selected.entry_pc:x})"
                )
            return matches[0]
        if (
            self._indices
            or self.explicit
            or any(
                (base / "aicore_objects").exists() or (base / "aicore_objects").is_symlink()
                for base in (self.export, self.export / "record")
            )
        ):
            print_warn(
                f"AI Core object match {'ambiguous' if matches else 'missing'} for kernel {kernel!r}; "
                "object-dependent analysis disabled"
            )
        return None

    def close(self) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
