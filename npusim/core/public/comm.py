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

import re
import sys
from datetime import datetime, timezone
from typing import List
from npusim.core.public.typical_operator_times import TYPICAL_OPERATOR_TIMES


# --- 颜色定义 (ANSI Escape Codes) ---
# 格式：\033[<code>m
COLOR_RESET = "\033[0m"

# 前景色
COLOR_RED = "\033[91m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_CYAN = "\033[96m"
COLOR_GREY = "\033[90m"
COLOR_WHITE = "\033[97m"

# 样式 (加粗等)
STYLE_BOLD = "\033[1m"

# --- 核心逻辑：自动检测终端支持 ---
USE_COLOR = sys.stdout.isatty()

# --- Welcome banner (由 main.py 在程序启动时打印) ---
welcome = """
███╗   ██╗██████╗ ██╗   ██╗    ███████╗██╗███╗   ███╗    
████╗  ██║██╔══██╗██║   ██║    ██╔════╝██║████╗ ████║    
██╔██╗ ██║██████╔╝██║   ██║    ███████╗██║██╔████╔██║    
██║╚██╗██║██╔═══╝ ██║   ██║    ╚════██║██║██║╚██╔╝██║    
██║ ╚████║██║     ╚██████╔╝    ███████║██║██║ ╚═╝ ██║    
╚═╝  ╚═══╝╚═╝      ╚═════╝     ╚══════╝╚═╝╚═╝     ╚═╝    
                                                        
    Join the conversation and help shape the future.
    └─ https://gitcode.com/org/cann/discussions/50

Notice: The cannsim command has been renamed to npusim and will be deprecated soon.
Please use npusim instead.
"""  # noqa: E501


def _get_style(level):
    """获取对应级别的样式前缀和后缀"""
    if not USE_COLOR:
        return "", ""

    level_upper = level.upper()
    if level_upper == "ERROR":
        return f"{STYLE_BOLD}{COLOR_RED}", COLOR_RESET
    elif level_upper == "WARN":
        return f"{STYLE_BOLD}{COLOR_YELLOW}", COLOR_RESET
    elif level_upper == "INFO":
        return COLOR_GREEN, COLOR_RESET
    elif level_upper == "DEBUG":
        return COLOR_GREY, COLOR_RESET
    else:
        return "", COLOR_RESET


def _format_message(level, message):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # 获取 Level 的样式
    lvl_prefix, lvl_suffix = _get_style(level)

    # 时间戳样式 (青色)
    if USE_COLOR:
        ts_part = f"{COLOR_CYAN}[{timestamp}]{COLOR_RESET}"
        lvl_part = f"{lvl_prefix}[{level}]{lvl_suffix}"
    else:
        ts_part = f"[{timestamp}]"
        lvl_part = f"[{level}]"

    return f"{ts_part} {lvl_part} {message}"


def print_error(message):
    print(_format_message("ERROR", message), file=sys.stderr)


def print_info(message):
    print(_format_message("INFO", message))


def print_warn(message):
    print(_format_message("WARN", message))


def print_debug(message):
    print(_format_message("DEBUG", message))


def print_simple_table(data, header=False, headers=None):
    """打印两列键值表（纯 Box Drawing 风格）。

    Args:
        data:    要打印的 dict。调用方不应再把表头塑进 data 里——使用 ``headers`` 参数。
        header:  [兼容] 若为 True 且未提供 headers，将取 data 第一项作为表头（legacy）。
        headers: 显式表头 (header_key, header_value)；若提供则优先生效，
                 data 将全部当作数据行。
    """
    if not data:
        return

    if headers is not None:
        if not (isinstance(headers, (tuple, list)) and len(headers) == 2):
            raise ValueError("headers must be a (key, value) 2-tuple")
        header_key, header_value = headers
        rows = list(data.items())
        has_header = True
    elif header:
        items = list(data.items())
        if not items:
            return
        header_key, header_value = items[0]
        rows = items[1:]
        has_header = True
    else:
        header_key = header_value = None
        rows = list(data.items())
        has_header = False

    if not rows and not has_header:
        return

    if has_header:
        max_key_len = max(
            len(str(header_key)),
            max(len(str(k)) for k, _ in rows) if rows else 0,
        )
        max_value_len = max(
            len(str(header_value)),
            max(len(str(v)) for _, v in rows) if rows else 0,
        )
    else:
        max_key_len = max(len(str(k)) for k, _ in rows)
        max_value_len = max(len(str(v)) for _, v in rows)

    key_bar = "─" * (max_key_len + 2)
    val_bar = "─" * (max_value_len + 2)
    top = f"┌{key_bar}┬{val_bar}┐"
    mid = f"├{key_bar}┼{val_bar}┤"
    bot = f"└{key_bar}┴{val_bar}┘"

    def _row(k, v):
        return f"│ {str(k):<{max_key_len}} │ {str(v):<{max_value_len}} │"

    print(top)
    if has_header:
        print(_row(header_key, header_value))
        print(mid)
    for key, value in rows:
        print(_row(key, value))
    print(bot)


def print_title(title):
    """打印带颜色框框的标题（纯 Box Drawing 双线风格，宽度根据标题动态计算）。

    - 有颜色时使用亮青色加粗边框 + 白色加粗标题。
    - 固定最小宽度 70 字符，标题过长时自动扩展，不会负数填充错位。
    """
    MIN_WIDTH = 70
    title_str = str(title)

    # 内部宽度 = max(标题长度, MIN_WIDTH)；两侧各留 1 空格 → 总内部占位 inner_len + 2
    inner_len = max(len(title_str), MIN_WIDTH)
    padding = " " * max(0, inner_len - len(title_str))

    top = f"╔{'═' * (inner_len + 2)}╗"
    bot = f"╚{'═' * (inner_len + 2)}╝"

    if USE_COLOR:
        c_border = f"{COLOR_CYAN}{STYLE_BOLD}"
        c_reset = COLOR_RESET
        c_title = f"{STYLE_BOLD}{COLOR_WHITE}"
        mid = (
            f"{c_border}║{c_reset} {c_title}{title_str}{c_reset}"
            f"{padding} {c_border}║{c_reset}"
        )
        print(f"{c_border}{top}{c_reset}")
        print(mid)
        print(f"{c_border}{bot}{c_reset}")
    else:
        print(top)
        print(f"║ {title_str}{padding} ║")
        print(bot)


def _format_operator_table_header(lines, title_0, title_1, title_2, title_3):
    """Format the header section of the operator times table."""
    c_border = f"{COLOR_GREEN}{STYLE_BOLD}"
    c_reset = COLOR_RESET
    max_size = len(title_1)
    if USE_COLOR:
        lines.append(f"{c_border}{'=' * max_size}{c_reset}")
        lines.append(f"{c_border}{title_0}{c_reset}")
    else:
        lines.append("=" * max_size)
        lines.append(title_0)

    lines.append(" ")
    lines.append(title_1)
    lines.append(title_2)
    lines.append(title_3)

    if USE_COLOR:
        lines.append(f"{c_border}{'=' * max_size}{c_reset}")
    else:
        lines.append("=" * max_size)


def _get_column_widths(header, operator_times):
    """Calculate column widths for the operator times table."""
    col_widths = {
        key: max(len(str(key)), max(len(str(op[key])) for op in operator_times))
        for key in header
    }
    col_widths["Operator type"] = max(col_widths.get("Operator type", 0), 10)
    col_widths["Case name"] = max(col_widths.get("Case name", 0), 18)
    col_widths["Parameters"] = max(col_widths.get("Parameters", 0), 48)
    col_widths["Simulation time(ms)"] = max(
        col_widths.get("Simulation time(ms)", 0), 15
    )
    col_widths["Cycles"] = max(col_widths.get("Cycles", 0), 7)
    return col_widths


def _format_table_rows(lines, header, col_widths, operator_times):
    """Format the header and data rows of the operator times table (Box Drawing 风格)."""
    def _cell(text: str, width: int) -> str:
        return f" {text:<{width}} "

    # 表头边框
    separator = (
        "├" + "┼".join("─" * (col_widths[key] + 2) for key in header) + "┤"
    )
    lines.append("┌" + "┬".join("─" * (col_widths[key] + 2) for key in header) + "┐")

    header_line = (
        "│" + "│".join(_cell(key, col_widths[key]) for key in header) + "│"
    )
    lines.append(header_line)
    lines.append(separator)

    for op in operator_times:
        row_line = (
            "│"
            + "│".join(_cell(str(op[key]), col_widths[key]) for key in header)
            + "│"
        )
        lines.append(row_line)

    lines.append("└" + "┴".join("─" * (col_widths[key] + 2) for key in header) + "┘")


def print_typical_operator_times(operator_times=None):
    """
    打印典型算子执行时间（返回格式化字符串）

    Args:
        operator_times: 包含算子执行时间信息的字典列表

    Returns:
        str: 格式化后的算子执行时间表格字符串
    """
    if not operator_times:
        return "No operator time data available."

    title_0 = "SIMULATION START"
    title_1 = (
        "[NOTE] CAMODEL simulates real NPU behavior using the CPU."
        "Operator execution time will be longer than on actual hardware, depending on operator complexity."
    )
    title_2 = "[NOTE] Minimum Requirements: CPU > 16 cores, Memory > 32GB."
    title_3 = (
        "[NOTE] Typical operator execution times"
        "(Test platform Configuration:CPU 64 cores@2.800GHz, Memory 256GB):"
    )

    lines = []
    _format_operator_table_header(lines, title_0, title_1, title_2, title_3)

    header = list(operator_times[0].keys())
    col_widths = _get_column_widths(header, operator_times)
    _format_table_rows(lines, header, col_widths, operator_times)

    return "\n".join(lines)


# ------------------------------ Reference benchmarks (Stage 3 开场白) ------------------------------

# 展示给用户的列（去掉 Cycles；Cycles 是微架构级数据，对大多数用户是噪声）
_REF_QUERY_COLUMNS = ("Operator type", "Case name", "Parameters", "Simulation time(ms)")
_REF_DISPLAY_COLUMNS = ("Operator type", "Case name", "Parameters", "Simulation time")
_REF_COL_MIN_WIDTH = {
    "Operator type": 13,
    "Case name": 14,
    "Parameters": 32,
    "Simulation time": 16,
}


def render_reference_benchmarks(operator_times=None):
    """渲染参考算子执行时间表格（用于 Stage 3 开场白）。

    格式优化目标：
    - Header 行突出显示（青字加粗），与正文表格风格区分
    - 表头加粗、数据行保持简洁
    - 无 Total 汇总（减少信息噪音）

    本函数仅返回字符串，不直接 ``print``，便于调用方组合到更大的输出块里。
    """
    if not operator_times:
        return ""

    lines = []
    if USE_COLOR:
        lines.append(
            f"{COLOR_GREY}{STYLE_BOLD}Reference benchmarks"
            f"{COLOR_RESET}{COLOR_GREY} (lab baseline · 64c@2.8GHz · for reference only){COLOR_RESET}"
        )
    else:
        lines.append(
            "Reference benchmarks (lab baseline · 64c@2.8GHz · for reference only)"
        )

    # 3 句极简免责（不重复 lab baseline 信息）
    _note_camodel = "[NOTE] CAMODEL runs on CPU — slower than real hardware"
    _note_hw = "[NOTE] CPU > 16 cores · Memory > 32GB"
    lines.append(
        f"{COLOR_GREY}{_note_camodel}{COLOR_RESET}" if USE_COLOR else _note_camodel
    )
    lines.append(
        f"{COLOR_GREY}{_note_hw}{COLOR_RESET}" if USE_COLOR else _note_hw
    )
    lines.append("")   # 空行分隔 NOTE 与表格

    header = list(_REF_DISPLAY_COLUMNS)

    def _wrap(text: str, width: int) -> List[str]:
        """按逗号断点换行，每行不超过 width 字符。"""
        parts = text.split(",")
        out, cur = [], ""
        for p in parts:
            p = p.strip()
            if not cur:
                cur = p
            elif len(cur) + 1 + len(p) <= width:
                cur += ", " + p
            else:
                out.append(cur)
                cur = p
        if cur:
            out.append(cur)
        return out if out else [""]

    # ---- Step 1: 展开所有数据行（每行一个 dict → 每行一个 List[List[str]]） ----
    flat_rows = []
    for op in operator_times:
        row = []
        for key, qkey in zip(_REF_DISPLAY_COLUMNS, _REF_QUERY_COLUMNS):
            raw = op.get(qkey, "")
            if qkey == "Simulation time(ms)":
                try:
                    row.append([f"{float(raw) / 1000:.1f}s"])
                except (TypeError, ValueError):
                    row.append([str(raw)])
            elif key == "Parameters":
                row.append(_wrap(raw, _REF_COL_MIN_WIDTH["Parameters"]))
            else:
                row.append([str(raw)])
        flat_rows.append(row)

    # ---- Step 2: 统一计算列宽（取所有扁平行中每列最长项） ----
    col_widths = {}
    for col_idx, col_name in enumerate(header):
        max_len = len(col_name)
        for row in flat_rows:
            for cell in row[col_idx]:
                max_len = max(max_len, len(cell))
        # 最小宽度约束
        min_w = _REF_COL_MIN_WIDTH.get(col_name, 0)
        col_widths[col_name] = max(max_len, min_w)

    # ---- Step 3: 统一格式化函数 ----
    def _cell(text: str, width: int) -> str:
        """左对齐到视觉 width（不计 ANSI 序列）；无前后空格。"""
        if USE_COLOR:
            bare = re.sub(r"\033\[[0-9;]*m", "", text)
            return text.replace(bare, bare.ljust(width))
        return text.ljust(width)

    # ---- Step 4: 逐行打印 ----
    cw = col_widths
    seg = [cw[col_name] for col_name in header]
    content_join = " | "
    col_bar = "-+-".join("-" * w for w in seg)

    # 整表使用与 NOTE 相同的灰色
    def _grey(s):
        return s if not USE_COLOR else COLOR_GREY + s + COLOR_RESET

    def _border():
        return _grey("+" + col_bar + "+")

    def _data_row(cells):
        return _grey("|" + content_join.join(cells) + "|")

    lines.append(_border())
    hdr_cells = [_cell(col_name, cw[col_name]) for col_name in header]
    lines.append(_data_row(hdr_cells))
    lines.append(_border())

    for row in flat_rows:
        max_h = max(len(c) for c in row)
        for h in range(max_h):
            cells = []
            for col_idx, col_name in enumerate(header):
                val = row[col_idx][h] if h < len(row[col_idx]) else ""
                cells.append(_cell(val, cw[col_name]))
            lines.append(_data_row(cells))

    lines.append(_border())

    return "\n".join(lines)


def main():
    # 测试日志颜色
    print_info("System initialized successfully.")
    print_warn("Configuration file missing, using defaults.")
    print_error("Failed to connect to device.")
    print_debug("Internal state dump: {var: 123}")

    print("\n")

    # 测试标题
    print_title("OPERATOR SIMULATION REPORT")
    print("\n")

    # 测试表格
    sample_data = {
        "ID": 101,
        "Name": "Conv2D_Test",
        "Status": "Passed",
        "Time(ms)": 12.5,
    }
    print_info("Running simple table test (with header):")
    print_simple_table(sample_data, header=True)

    print("\n")

    # 测试算子时间表
    print_info("Generating typical operator times report:")
    report = print_typical_operator_times(TYPICAL_OPERATOR_TIMES)
    print(report)


if __name__ == "__main__":
    main()
