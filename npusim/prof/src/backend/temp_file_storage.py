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

from __future__ import annotations

import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, TextIO, cast

from npusim.prof.src.common.utils import short_hash


def _open_file(
    path: Path,
    mode: str,
    encoding: str,
    newline: str,
) -> TextIO:
    """Helper to open a file and cast to TextIO."""
    return cast(
        TextIO,
        open(path, mode=mode, encoding=encoding, newline=newline),
    )


class TempFileStorage:
    """
    Manages temporary text files inside a dedicated temporary directory.

    Files are addressed by (prefix, key) pairs. Internally, keys are hashed
    to produce stable and filesystem-safe filenames. All created files are
    cleaned up when the instance is closed or when the process exits.
    """

    def __init__(self, dir_name: str = "biprof_temp") -> None:
        # Create isolated temporary directory for this storage instance
        self._td = tempfile.TemporaryDirectory(prefix=f"{dir_name}_")
        self.dir: Path = Path(self._td.name).resolve()

        # Maps logical store keys to actual file paths
        self._index: Dict[str, Path] = {}

        # Tracks unindexed named temp files so cleanup() can remove them
        self._named_paths: List[Path] = []

    def _make_store_key(self, prefix: str, key: str) -> str:
        """Build internal storage key using sanitized prefix and hashed key."""
        safe = self._safe_prefix(prefix)
        hashed = short_hash(key)
        return f"{safe}|{hashed}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def path(self, prefix: str, key: str) -> Optional[str]:
        """Return the file path string if the file exists, otherwise None."""
        store_key = self._make_store_key(prefix, key)
        p = self._index.get(store_key)
        if p is None:
            return None
        if not p.exists():
            # Lazily evict stale index entry
            del self._index[store_key]
            return None
        return str(p)

    def exists(self, prefix: str, key: str) -> bool:
        """Return True if the file exists in storage."""
        return self.path(prefix, key) is not None

    @contextmanager
    def open(
        self,
        prefix: str,
        key: str,
        *,
        mode: str = "w+",
        encoding: str = "utf-8",
        newline: str = "",
    ) -> Iterator[TextIO]:
        """
        Open file for given (prefix, key). Always creates the file if missing.
        """
        path = self._ensure_path(prefix, key)
        f = _open_file(path, mode, encoding, newline)
        try:
            yield f
        finally:
            f.close()

    @contextmanager
    def open_auto(
        self,
        prefix: str,
        key: str,
        *,
        write_mode: str = "w+",
        read_mode: str = "r",
        encoding: str = "utf-8",
        newline: str = "",
    ) -> Iterator[TextIO]:
        """
        Open file for reading if it already exists on disk,
        otherwise create it using write_mode.
        """
        store_key = self._make_store_key(prefix, key)
        p = self._index.get(store_key)

        if p is not None and p.exists():
            f = _open_file(p, read_mode, encoding, newline)
        else:
            # File missing from index or deleted externally — (re)create it.
            if p is not None and not p.exists():
                # Evict stale entry so _ensure_path registers it fresh
                del self._index[store_key]
            path = self._ensure_path(prefix, key)
            f = _open_file(path, write_mode, encoding, newline)

        try:
            yield f
        finally:
            f.close()

    @contextmanager
    def named(
        self,
        *,
        mode: str = "w+",
        encoding: str = "utf-8",
        newline: str = "",
        prefix: str = "tmp",
        suffix: str = ".tmp",
    ) -> Iterator[TextIO]:
        """
        Create a standalone named temporary file inside the storage directory.
        The file IS tracked so that cleanup() removes it on process exit.
        """
        safe = self._safe_prefix(prefix)
        tmp = tempfile.NamedTemporaryFile(
            mode=mode,
            encoding=encoding,
            newline=newline,
            dir=str(self.dir),
            prefix=f"{safe}_",
            suffix=suffix,
            delete=False,
        )
        tmp_path = Path(tmp.name)
        self._named_paths.append(tmp_path)
        f = cast(TextIO, tmp)
        try:
            yield f
        finally:
            f.close()

    def delete(self, prefix: str, key: str) -> bool:
        """Remove file from storage index and from the filesystem."""
        store_key = self._make_store_key(prefix, key)
        p = self._index.pop(store_key, None)
        if p is None:
            return False
        try:
            if p.exists():
                p.unlink()
            return True
        except OSError:
            return False

    def cleanup(self) -> None:
        """
        Delete all tracked files and remove the temporary directory.
        Safe to call multiple times.
        """
        # Remove indexed files
        index, self._index = self._index, {}
        for p in index.values():
            _try_unlink(p)

        # Remove unindexed named files
        named, self._named_paths = self._named_paths, []
        for p in named:
            _try_unlink(p)

        # Remove the temporary directory itself
        try:
            self._td.cleanup()
        except OSError:
            pass

    def __enter__(self) -> TempFileStorage:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _safe_prefix(self, prefix: str) -> str:
        """Sanitize prefix to be filesystem-safe and reasonably short."""
        p = prefix.strip()
        p = re.sub(r"[^A-Za-z0-9._-]+", "_", p)
        return (p or "file")[:40]

    def _ensure_path(self, prefix: str, key: str) -> Path:
        """
        Return the Path for (prefix, key), creating
        and registering it if needed.

        The filename embeds both the sanitized prefix AND the full key hash so
        that two different prefixes that sanitize to the same string still get
        distinct file paths.
        """
        store_key = self._make_store_key(prefix, key)
        existing = self._index.get(store_key)
        if existing is not None:
            return existing

        safe = self._safe_prefix(prefix)
        key_hash = short_hash(key)
        # Include store_key hash
        # to guarantee uniqueness across (prefix, key) pairs
        unique = short_hash(store_key)
        filename = f"{safe}_{key_hash}_{unique}.tmp"
        path = (self.dir / filename).resolve()
        self._index[store_key] = path
        return path


def _try_unlink(p: Path) -> None:
    """Delete a file, silently ignoring errors."""
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


# Global storage instance
tfs: TempFileStorage = TempFileStorage()
