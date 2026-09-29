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

"""Normalize record user_app input into an executable launch specification."""

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union


# archive 标签里允许出现的字符：字母数字、点号、下划线、加号、连字符
_ARCHIVE_LABEL_SAFE = re.compile(r"[^A-Za-z0-9._+-]+")
# 解释器/wrapper：当 shell 命令的第一个 token 是这些时，归档标签优先取下一个非选项 token
_INTERPRETER_TOKENS = frozenset({
    "bash", "sh", "zsh", "ksh",
    "python", "python2", "python3",
    "env", "pytest",
})
_SHELL_TOKENS = frozenset({
    "bash", "sh", "zsh", "ksh",
})
# Python 相关命令前缀：当 shell 命令首 token 的 basename 命中时，需注入 TASK_QUEUE_ENABLE=0
_PYTHON_TOKENS = frozenset({
    "python", "python2", "python3", "pytest",
})
_SHELL_METACHARS = frozenset("|&;<>()$`")


@dataclass(frozen=True)
class LaunchSpec:
    """用户启动目标的规范化结果，兼容 Python 3.7 类型语法。

    kind:
        ``binary`` / ``script`` 表示本地文件模式；``command`` 表示原样交给 shell。
    """

    raw: str
    kind: str
    label: str
    argv: List[str]
    cwd: Path
    asset_roots: Tuple[Path, ...]
    is_python: bool = False
    executable_path: Optional[Path] = None


def _safe_archive_label(candidate: str) -> str:
    candidate = _ARCHIVE_LABEL_SAFE.sub("_", candidate).strip("._-")
    if not candidate:
        candidate = "user_app"
    return candidate[:64]


def _is_elf(path: Path) -> bool:
    """检测文件是否为 ELF 二进制。"""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except Exception:
        return False


def _split_command(raw: str) -> List[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _resolve_user_path(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path


def _looks_like_path(raw: str) -> bool:
    if raw.startswith(("./", "../", "/")):
        return True
    return os.sep in raw


def _has_shell_syntax(raw: str) -> bool:
    return any(ch in raw for ch in _SHELL_METACHARS)


def _command_primary_token(tokens: List[str]) -> str:
    """从命令 token 中找出用于 archive 命名/资产目录推断的主程序 token。"""
    if not tokens:
        return ""

    skip_next = False
    for idx, tok in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue

        base = Path(tok).name
        if idx == 0 and base == "env":
            continue
        if idx > 0 and Path(tokens[0]).name == "env" and "=" in tok:
            continue
        if base in _INTERPRETER_TOKENS:
            # 跳过 python/bash/env 等 wrapper，让 label 指向真正的脚本/程序。
            # 若命令只有 wrapper，循环结束后会 fallback 到首 token。
            continue
        if tok.startswith("-"):
            if tok in ("-c", "-m") and idx + 1 < len(tokens):
                skip_next = True
            continue
        return tok

    return tokens[0]


def _is_python_launch(tokens: List[str], executable_path: Optional[Path] = None) -> bool:
    if executable_path is not None and executable_path.suffix == ".py":
        return True
    if not tokens:
        return False

    idx = 0
    if Path(tokens[0]).name == "env":
        idx = 1
        while idx < len(tokens) and (tokens[idx].startswith("-") or "=" in tokens[idx]):
            idx += 1

    if idx >= len(tokens):
        return False
    return Path(tokens[idx]).name in _PYTHON_TOKENS


def _append_user_option_to_command(raw: str, user_option: str) -> str:
    if not user_option:
        return raw
    return raw + " " + user_option


def _quote_command(tokens: List[str], user_option: str) -> str:
    command = " ".join(shlex.quote(tok) for tok in tokens)
    return _append_user_option_to_command(command, user_option)


def _command_asset_roots(tokens: List[str], cwd: Path) -> Tuple[Path, ...]:
    roots = [cwd.resolve()]
    primary = _command_primary_token(tokens)
    if primary and _looks_like_path(primary):
        script_path = _resolve_user_path(primary, cwd)
        script_dir = script_path.parent.resolve()
        if script_dir.exists() and script_dir not in roots:
            roots.append(script_dir)
    return tuple(roots)


def _shell_script_index(tokens: List[str]) -> Optional[int]:
    if not tokens or Path(tokens[0]).name not in _SHELL_TOKENS:
        return None

    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "-c":
            return None
        if tok.startswith("-"):
            idx += 1
            continue
        return idx
    return None


def _shell_script_command(
    raw: str, tokens: List[str], user_option: str, cwd: Path
) -> Optional[LaunchSpec]:
    script_idx = _shell_script_index(tokens)
    if script_idx is None:
        return None

    script_token = tokens[script_idx]
    if not _looks_like_path(script_token):
        return None

    script_path = _resolve_user_path(script_token, cwd)
    if not script_path.exists() or not script_path.is_file():
        return None

    command_tokens = list(tokens)
    command_tokens[script_idx] = script_path.name
    script_dir = script_path.parent.resolve()

    return LaunchSpec(
        raw=raw,
        kind="command",
        label=_safe_archive_label(script_path.name),
        argv=["bash", "-c", _quote_command(command_tokens, user_option)],
        cwd=script_dir,
        asset_roots=(script_dir, cwd.resolve()),
        is_python=False,
        executable_path=None,
    )


def _build_file_launch_spec(raw: str, path: Path, user_option: str) -> LaunchSpec:
    abs_path = path.resolve()
    label = _safe_archive_label(abs_path.name)
    user_args = shlex.split(user_option) if user_option else []

    if _is_elf(abs_path):
        argv = [str(abs_path)] + user_args
        kind = "binary"
    else:
        full_cmd = shlex.quote(str(abs_path))
        if user_option:
            full_cmd += " " + user_option
        argv = ["bash", "-c", full_cmd]
        kind = "script"

    return LaunchSpec(
        raw=raw,
        kind=kind,
        label=label,
        argv=argv,
        cwd=abs_path.parent,
        asset_roots=(abs_path.parent,),
        is_python=_is_python_launch([], executable_path=abs_path),
        executable_path=abs_path,
    )


def _build_command_launch_spec(raw: str, user_option: str, cwd: Path) -> LaunchSpec:
    tokens = _split_command(raw)
    shell_script_spec = _shell_script_command(raw, tokens, user_option, cwd)
    if shell_script_spec is not None:
        return shell_script_spec

    primary = _command_primary_token(tokens)
    label_source = Path(primary).name if primary else raw
    full_cmd = _append_user_option_to_command(raw, user_option)

    return LaunchSpec(
        raw=raw,
        kind="command",
        label=_safe_archive_label(label_source),
        argv=["bash", "-c", full_cmd],
        cwd=cwd.resolve(),
        asset_roots=_command_asset_roots(tokens, cwd),
        is_python=_is_python_launch(tokens),
        executable_path=None,
    )


def build_launch_spec(raw_user_app: Union[str, Path], user_option: str, cwd: Path) -> LaunchSpec:
    """把 CLI 的 user_app 解析成单一启动规范。

    本地文件模式和命令模式的边界在这里一次性确定，后续 stage 只消费 LaunchSpec。
    """
    raw = str(raw_user_app).strip()
    path = _resolve_user_path(raw, cwd)

    if path.exists() and path.is_file():
        return _build_file_launch_spec(raw, path, user_option)

    tokens = _split_command(raw)
    first = Path(tokens[0]).name if tokens else ""
    if (
        len(tokens) > 1
        or first in _INTERPRETER_TOKENS
        or first in _PYTHON_TOKENS
        or _has_shell_syntax(raw)
        or not _looks_like_path(raw)
    ):
        return _build_command_launch_spec(raw, user_option, cwd)

    # 看起来是路径但尚不存在：保留为文件模式，让 Stage 1 给出明确的路径错误。
    return _build_file_launch_spec(raw, path, user_option)
