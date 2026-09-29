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

"""NPU SIM record 动态仪表板。

职责分层：

- :class:`PhaseDetector` : 无状态 regex 喂入、产出阶段跃迁与关键事件；
- :func:`read_process_stats` : 从 ``/proc/{pid}`` 读取 CPU/RSS/state/threads；
- :func:`classify_health` : 把 CPU / log lag / phase TTL 汇总成弱观测信号；
- :class:`Dashboard` : 协调以上三者；TTY 下原地刷新固定高度面板 + 追加事件流；
  非 TTY 下退化成事件流（仅 event 追加 + 30s 心跳），CI / ``tee`` 日志干净。

所有文本输出集中在本模块（不再散落在 record.py 的定时器里），避免 TTY / 非 TTY
分支在多处重复出现；调用方只需要 ``start() / stop()`` 生命周期管理。
"""

import os
import re
import shutil
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

# 获取 CPU 核心数，用于正确计算 CPU 使用率百分比
_NUM_CPUS = os.cpu_count() or 1


# ------------------------------ 环境探测 ------------------------------

_IS_TTY = sys.stdout.isatty()


def _lang_supports_utf8() -> bool:
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        val = os.environ.get(var, "")
        if "UTF" in val.upper():
            return True
    return False


_UTF8 = _lang_supports_utf8()

if _UTF8:
    BOX_TL, BOX_TR, BOX_BL, BOX_BR = "┌", "┐", "└", "┘"
    BOX_H, BOX_V = "─", "│"
    BOX_LT, BOX_RT = "├", "┤"
    DOT_CHAR = "●"
    X_CHAR = "×"
    USER_PREFIX = "»"
    ARROW = "→"
else:
    BOX_TL = BOX_TR = BOX_BL = BOX_BR = "+"
    BOX_H, BOX_V = "-", "|"
    BOX_LT = BOX_RT = "+"
    DOT_CHAR = "*"
    X_CHAR = "x"
    USER_PREFIX = ">>"
    ARROW = "->"


# ------------------------------ ANSI 色辅助 ------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
GREY = "\033[90m"
WHITE = "\033[97m"
MAGENTA = "\033[95m"


def _c(text: str, color: str) -> str:
    """条件着色：TTY 才加 ANSI；非 TTY 返回原样，避免把控制字符写进日志文件。"""
    if not _IS_TTY:
        return text
    return f"{color}{text}{RESET}"


# ------------------------------ 阶段定义 ------------------------------

PHASE_DRIVER_INIT = "driver_init"
PHASE_MODEL_LOAD = "model_load"
PHASE_CORE_INIT = "core_init"
PHASE_STARS_CFG = "stars_cfg"
PHASE_CORE_BUILD = "core_build"
PHASE_SMMU_BUILD = "smmu_build"
PHASE_SIM_START = "sim_start"
PHASE_SOC_READY = "soc_ready"
PHASE_TASK_DISPATCH = "task_dispatch"
PHASE_KERNEL_RUN = "kernel_run"
PHASE_KERNEL_DONE = "kernel_done"
PHASE_HW_FINISH = "hw_finish"
PHASE_SHUTDOWN = "shutdown"

PHASE_LABELS = {
    PHASE_DRIVER_INIT: "driver init",
    PHASE_MODEL_LOAD: "loading model files",
    PHASE_CORE_INIT: "core init",
    PHASE_STARS_CFG: "stars scheduler config",
    PHASE_CORE_BUILD: "core build",
    PHASE_SMMU_BUILD: "smmu page build",
    PHASE_SIM_START: "sim start",
    PHASE_SOC_READY: "SoC ready, awaiting tasks",
    PHASE_TASK_DISPATCH: "task dispatching",
    PHASE_KERNEL_RUN: "kernel running",
    PHASE_KERNEL_DONE: "kernel done",
    PHASE_HW_FINISH: "hardware finish",
    PHASE_SHUTDOWN: "model stopped",
}


# ------------------------------ 事件模型 ------------------------------

# Level 决定前缀着色以及非 TTY 下的 tag 名；与渲染解耦，便于扩展新事件类型。
EVENT_PHASE = "phase"
EVENT_INFO = "info"
EVENT_WARN = "warn"
EVENT_USER = "user"
EVENT_HEALTH = "health"
EVENT_ERROR = "error"


@dataclass
class Event:
    """单条事件——追加到事件流，永不被面板覆盖。"""

    elapsed_s: float
    level: str
    message: str


# ------------------------------ 用户 stdout 识别 ------------------------------

# camodel / driver / STARS 等框架侧日志的起始子串白名单；
# 开头**不**命中这些前缀的行，视为 USER_APP 自身的 printf/printf-like 输出，
# 值得醒目 echo 给用户（这是"我的代码跑出了什么结果"的直接证据）。
_FRAMEWORK_PREFIXES: Tuple[str, ...] = (
    "[CORE_WRAPPER]", "[TmSim]", "[debug]", "[info]", "[INFO]",
    "[DRVSTUB_LOG]", "[ESL_TOP]", "[Esl Top]", "[Hardware]",
    "[NOTE]", "[FUSION]", "[DVPP]", "[UBDMA]",
    "==", "WARNING:", "Warning:", "Warning!",
    "CONFIG STARS", "Module STARS",
    "SLLC CONNECT", "Create ./log", "Create ./run_log", "Create ./log_ca",
    "SoC unit ", "SoC sub ", "HHA->", "DDR ",
    "Reading DDR", "Guide file:", "Start to build", "Start simulation",
    "rank0_chip",   # 多 rank 日志里的 rankN_chipN_created 框架输出
    "rankId:", "chip type:", "core count:", "thread count:", "chipNum:",
    "chipId ",
    "Multi SoC", "All Chip", "can not find",
    "DIE0", "DIE1", "DIE2", "DIE3",
    "ESL_TOP:", "Esl Top]", "SMMU ",
    "STARS_", "Submit request to",
    # 白名单会覆盖 "First " / "z[" 等常见 bbit 输出，这里也列进来以便集中维护；
    # is_user_stdout_line() 会优先检查 _USER_STDOUT_WHITELIST，所以不会被"黑名单"误伤。
    "First ", "z[",
)

# 在 _FRAMEWORK_PREFIXES 中被误伤的"常见 user stdout 样本"。
# 保留在这个白名单里的行会强制被视为 user stdout。
_USER_STDOUT_WHITELIST: Tuple[str, ...] = (
    "First ",
    "z[",
)


def is_user_stdout_line(line: str) -> bool:
    """判断一行是否来自 USER_APP 自身的 stdout/stderr。

    规则：命中白名单即 user stdout；否则按前缀黑名单判定。白名单优先级高
    是为了兜底常见 bbit 样例输出（例如 ``First 10 output values:``）。
    """
    s = line.strip()
    if not s:
        return False
    for w in _USER_STDOUT_WHITELIST:
        if s.startswith(w):
            return True
    for p in _FRAMEWORK_PREFIXES:
        if s.startswith(p):
            return False
    return True


# ------------------------------ PhaseDetector ------------------------------

# 阶段有序表（用于单调性约束）。序号越大越靠后。
_PHASE_ORDER: List[str] = [
    PHASE_DRIVER_INIT,
    PHASE_MODEL_LOAD,
    PHASE_CORE_INIT,
    PHASE_STARS_CFG,
    PHASE_CORE_BUILD,
    PHASE_SMMU_BUILD,
    PHASE_SIM_START,
    PHASE_SOC_READY,
    PHASE_TASK_DISPATCH,
    PHASE_KERNEL_RUN,
    PHASE_KERNEL_DONE,
    PHASE_HW_FINISH,
    PHASE_SHUTDOWN,
]


class PhaseDetector:
    """增量喂入日志行，推导当前阶段与相关计数。

    方法契约：
        ``feed(line) -> list[(level, message)]`` —— 返回 0+ 个事件；
        调用方负责把事件加上 elapsed 后落入事件流。

    内部状态只增不减（`cores_init` 等始终是"历史最大值"），使得在日志并发
    乱序、行内信息重复时依然收敛。

    阶段机遵守"只进不退"原则：一旦进入靠后的阶段，绝不会因为匹配到靠前的
    pattern 而回退。这样可以容忍 camodel 多 die 初始化时重复出现的 driver init。
    """

    # 预编译常用正则
    _RE_CORE_INIT = re.compile(r"CoreWrapper \[DvcCore(\d+)\] init")
    _RE_CORE_BUILD = re.compile(r"CoreWrapper \[DvcCore(\d+)\] build")
    _RE_DIE_CREATED = re.compile(r"SoC unit rank(\d+)_chip(\d+)_die(\d+) Created")
    _RE_BLOCK_START = re.compile(
        r"\[block_start\].*AIV, task_id=(\d+), core_id=(\d+), block_id=(\d+)"
    )
    _RE_BLOCK_END = re.compile(
        r"\[block_end\].*AIV, task_id=(\d+), core_id=(\d+), block_id=(\d+)"
    )
    _RE_TASK_DONE = re.compile(
        r"\[TASK_DONE\] RTSQ_\d+ ACSQ_\d+ stream_id:(\d+), task_id:(\d+)"
    )
    _RE_HW_FINISH = re.compile(
        r"parallel simulation finish\.\s*sim time:\s*([\d.]+)s"
    )
    _RE_BLKDIM = re.compile(r"blkdim (\d+)")
    _RE_DDR = re.compile(r"==\s*(lpddr\w+\s+\d+\s*Mbps[^=]*)")
    _RE_WARN_INI = re.compile(r"UNKNOWN KEY '([^']+)'")

    # 已知的、无害的 warnings，不在 dashboard 中展示
    _KNOWN_HARMLESS_WARNINGS: Tuple[str, ...] = (
        "TIMEOUT_SID_ENBALE",
        "Fail reading DDR checkpoints",
        "open top case toml file fail",
        "WARNING: no l3 cache",
        "WARNING: no media unit",
        "WARNING: no dvpp unit",
        "WARNING: no gpu unit",
    )

    def __init__(self):
        self.phase: Optional[str] = None
        self.phase_idx: int = -1        # 在 _PHASE_ORDER 中的序号，-1 表示未初始化
        self.phase_since: float = time.time()
        self.cores_init = 0
        self.cores_built = 0
        self.current_die: Optional[int] = None
        self.blocks_active = 0
        self.blocks_done = 0
        self.tasks_done = 0
        self.ddr_spec: Optional[str] = None
        self.hw_sim_times: List[float] = []   # 保留所有 die 的 sim time，供 summary 使用
        self.blkdim: Optional[int] = None
        self.total_cores = 32  # Ascend950 典型配置：每 die 16 核 × 2 die × 2 rank
        self._seen_warnings: dict = {}

    def _set_phase(self, p: str) -> bool:
        if p not in _PHASE_ORDER:
            return False
        new_idx = _PHASE_ORDER.index(p)
        if new_idx <= self.phase_idx:
            return False   # 只进不退
        self.phase = p
        self.phase_idx = new_idx
        self.phase_since = time.time()
        return True

    def phase_ttl(self) -> float:
        return time.time() - self.phase_since

    def progress_label(self) -> str:
        """当前阶段的短量化标签，例如 ``14/32 cores``。"""
        if self.phase == PHASE_CORE_INIT:
            return f"{self.cores_init}/{self.total_cores} cores"
        if self.phase == PHASE_CORE_BUILD:
            return f"{self.cores_built}/{self.total_cores} cores built"
        if self.phase == PHASE_KERNEL_RUN:
            return (
                f"task {self.tasks_done}, "
                f"blocks active={self.blocks_active} done={self.blocks_done}"
            )
        if self.phase == PHASE_KERNEL_DONE:
            return f"{self.tasks_done} task(s) done"
        return ""

    def feed(self, line: str) -> List[Tuple[str, str]]:
        stripped = line.strip()
        if not stripped:
            return []

        events: List[Tuple[str, str]] = []

        # 1) 首先做 user stdout 识别（最高优先级：一行要么是 user 要么是框架）
        if is_user_stdout_line(stripped):
            events.append((EVENT_USER, stripped[:160]))
            return events

        # 2) 框架侧：按最强 pattern 匹配逐级判定阶段跃迁
        events.extend(self._match_phase_patterns(stripped))

        # 3) 警告去重收集（同一 warning 只放首次到事件流，后续只计数）
        events.extend(self._match_warnings(stripped))

        return events

    def _match_phase_patterns(self, s: str) -> List[Tuple[str, str]]:
        events: List[Tuple[str, str]] = []

        # driver init
        if "halGetSocVersion" in s or s.startswith("rankId:") or s.startswith("chip type:"):
            if self._set_phase(PHASE_DRIVER_INIT):
                events.append((EVENT_PHASE, f"phase {ARROW} driver init"))
            return events

        # model load
        if (
            s.startswith("== Loading")
            or s.startswith("== lpddr")
            or s.startswith("== Unable to load")
        ):
            if self._set_phase(PHASE_MODEL_LOAD):
                events.append((EVENT_PHASE, f"phase {ARROW} loading model files"))
            m = self._RE_DDR.search(s)
            if m and not self.ddr_spec:
                self.ddr_spec = m.group(1).strip()
                events.append((EVENT_INFO, f"ddr: {self.ddr_spec}"))
            return events

        # core init
        m = self._RE_CORE_INIT.search(s)
        if m:
            core_id = int(m.group(1))
            self.cores_init = max(self.cores_init, core_id + 1)
            self.total_cores = max(self.total_cores, self.cores_init)
            if self._set_phase(PHASE_CORE_INIT):
                events.append(
                    (EVENT_PHASE, f"phase {ARROW} core init (die {core_id // 16})")
                )
            return events

        # stars config
        if s.startswith("CONFIG STARS SIM_CFG"):
            if self._set_phase(PHASE_STARS_CFG):
                events.append((EVENT_PHASE, f"phase {ARROW} stars scheduler config"))
            return events

        # core build
        m = self._RE_CORE_BUILD.search(s)
        if m:
            core_id = int(m.group(1))
            self.cores_built = max(self.cores_built, core_id + 1)
            if self._set_phase(PHASE_CORE_BUILD):
                events.append(
                    (EVENT_PHASE, f"phase {ARROW} core build (die {core_id // 16})")
                )
            return events

        # smmu
        if s.startswith("Start to build SMMU Page"):
            if self._set_phase(PHASE_SMMU_BUILD):
                events.append((EVENT_PHASE, f"phase {ARROW} smmu page build"))
            return events
        if s.startswith("SMMU Page build finished"):
            events.append((EVENT_INFO, "smmu page build done"))
            return events

        # sim start
        if s.startswith("Start simulation with m_socCycle"):
            if self._set_phase(PHASE_SIM_START):
                events.append((EVENT_PHASE, f"phase {ARROW} sim start"))
            return events

        # die created
        m = self._RE_DIE_CREATED.search(s)
        if m:
            self.current_die = int(m.group(3))
            events.append((EVENT_INFO, f"die {self.current_die} created"))
            return events

        # soc ready
        if s.startswith("Multi SoC in one chip Created") or s.startswith(
            "All Chip In Rank"
        ):
            if self._set_phase(PHASE_SOC_READY):
                events.append((EVENT_PHASE, f"phase {ARROW} SoC ready, awaiting tasks"))
            return events

        # task dispatch
        if "sendStarsSQE" in s or "acsq_idle_state_proc" in s:
            if self._set_phase(PHASE_TASK_DISPATCH):
                events.append((EVENT_PHASE, f"phase {ARROW} task dispatching"))
            m = self._RE_BLKDIM.search(s)
            if m:
                self.blkdim = int(m.group(1))
            return events

        # kernel run: block_start / block_end / task_done
        if self._RE_BLOCK_START.search(s):
            self.blocks_active += 1
            if self._set_phase(PHASE_KERNEL_RUN):
                events.append((EVENT_PHASE, f"phase {ARROW} kernel running"))
            return events
        if self._RE_BLOCK_END.search(s):
            self.blocks_done += 1
            self.blocks_active = max(0, self.blocks_active - 1)
            return events
        if self._RE_TASK_DONE.search(s):
            self.tasks_done += 1
            if self._set_phase(PHASE_KERNEL_DONE):
                events.append(
                    (EVENT_PHASE, f"phase {ARROW} task {self.tasks_done - 1} done")
                )
            return events

        # hardware finish
        m = self._RE_HW_FINISH.search(s)
        if m:
            t = float(m.group(1))
            self.hw_sim_times.append(t)
            die_num = len(self.hw_sim_times) - 1
            events.append(
                (EVENT_INFO, f"hardware sim finished: {t:.2f}s (die {die_num})")
            )
            return events

        # shutdown
        if "ESL_TOP: parallel threads joined" in s or "Model stopped successfully" in s:
            if self._set_phase(PHASE_SHUTDOWN):
                events.append((EVENT_PHASE, f"phase {ARROW} model stopped"))
            return events

        return events

    def _match_warnings(self, s: str) -> List[Tuple[str, str]]:
        events: List[Tuple[str, str]] = []
        m = self._RE_WARN_INI.search(s)
        if m:
            key = m.group(1)
            count = self._seen_warnings.get(key, 0) + 1
            self._seen_warnings[key] = count
            if count == 1:
                events.append(
                    (EVENT_WARN, f"UNKNOWN KEY '{key}' (repeats will be suppressed)")
                )
            return events

        if s.startswith(("WARNING:", "Warning:", "Warning!")):
            count = self._seen_warnings.get(s, 0) + 1
            self._seen_warnings[s] = count
            if count == 1:
                events.append((EVENT_WARN, s[:160]))
        return events

    def warnings_summary(self) -> List[Tuple[str, int]]:
        """返回 [(warning 原文, 命中次数), ...]，用于跑完后的最终汇总。
        已知无害 warnings 不在汇总中展示。"""
        return sorted(
            ((msg, n) for msg, n in self._seen_warnings.items()
             if not any(p in msg for p in self._KNOWN_HARMLESS_WARNINGS)),
            key=lambda kv: -kv[1],
        )


# ------------------------------ 进程指标 ------------------------------

_CLK_TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


@dataclass
class ProcStats:
    """/proc/{pid} 快照，全部 Optional，读失败时仅个别字段 None，不抛异常。"""

    alive: bool = False
    main_pid: int = 0
    pgid: Optional[int] = None
    process_count: int = 0
    proc_states: Counter = field(default_factory=Counter)
    thread_states: Counter = field(default_factory=Counter)
    threads: int = 0
    rss_bytes: int = 0
    utime_ticks: int = 0
    stime_ticks: int = 0
    num_cpu_ticks: int = 0      # utime + stime
    cpu_pct: float = 0.0        # 相对单核；>100% 表示多核并行

    def rss_hr(self) -> str:
        b = self.rss_bytes
        if b < 1024:
            return f"{b}B"
        if b < 1024 ** 2:
            return f"{b/1024:.1f}K"
        if b < 1024 ** 3:
            return f"{b/1024/1024:.1f}M"
        return f"{b/1024/1024/1024:.2f}G"


def _read_proc_stat(pid: int) -> Tuple[int, int, int, str]:
    """从 /proc/{pid}/stat 读 (utime, stime, num_threads, state)，读不到抛异常。"""
    with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
        return _parse_stat_data(f.read())


def _parse_stat_data(data: str) -> Tuple[int, int, int, str]:
    """解析 /proc/*/stat 内容，返回 (utime, stime, num_threads, state)。"""
    # comm 字段可能含空格/括号，需从右侧 `)` 之后 split，避免把 "(a b)" 切碎。
    rparen = data.rfind(")")
    fields = data[rparen + 2:].split()
    # proc(5) 语义：fields[0] 对应第 3 项（我们已砍掉 pid + comm）。
    # 字段 14 utime → fields[11]； 字段 15 stime → fields[12]； 字段 20 num_threads → fields[17]。
    state = fields[0]
    utime = int(fields[11])
    stime = int(fields[12])
    num_threads = int(fields[17])
    return utime, stime, num_threads, state


def _read_proc_pgid(pid: int) -> int:
    """从 /proc/{pid}/stat 读 pgid，读不到抛异常。"""
    with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
        data = f.read()
    rparen = data.rfind(")")
    fields = data[rparen + 2:].split()
    return int(fields[2])


def _read_thread_state(pid: int, tid: str) -> str:
    """从 /proc/{pid}/task/{tid}/stat 读线程状态码。"""
    with open(
        f"/proc/{pid}/task/{tid}/stat", "r", encoding="utf-8", errors="replace"
    ) as f:
        return _parse_stat_data(f.read())[3]


def _read_thread_states(pid: int) -> Counter:
    """读取单个进程下所有线程状态；进程消失时返回已读到的部分。"""
    states = Counter()
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return states

    for tid in tids:
        if not tid.isdigit():
            continue
        try:
            states[_read_thread_state(pid, tid)] += 1
        except (OSError, IOError, ValueError, IndexError):
            continue
    return states


def _read_proc_status_rss(pid: int) -> int:
    """从 /proc/{pid}/status 解析 VmRSS（bytes）。"""
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    # ``VmRSS:    1234 kB``
                    return int(parts[1]) * 1024
    except Exception:
        return 0
    return 0


def _get_process_group_pids(pgid: int) -> List[int]:
    """获取指定进程组中的所有 PID。"""
    try:
        pids = []
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                pid = int(name)
            except ValueError:
                continue
            try:
                if _read_proc_pgid(pid) == pgid:
                    pids.append(pid)
            except (OSError, IOError, ValueError, IndexError):
                continue
        return pids
    except (OSError, ValueError):
        return []


class ProcessMonitor:
    """对目标 pid 做增量 CPU% / RSS / threads 采样。

    支持两种模式：
    - 进程组模式（默认）：监控整个进程组（PGID），聚合所有子进程的 CPU/RSS
    - 单进程模式：如果进程组获取失败，回退到监控单个进程

    保留上一次 `(wall_time, cpu_ticks)`，下一次采样按差分计算 CPU%。
    第一次采样 cpu_pct 为 0.0（没有 baseline），之后每次刷新都是真实差分。
    """

    def __init__(self, pid: int):
        self.pid = pid
        self._last_wall: Optional[float] = None
        self._last_cpu_by_pid: Dict[int, int] = {}
        self._pgid: Optional[int] = None
        self._last_pids: List[int] = []  # 上次采样的进程列表

        # 尝试获取进程组 ID
        try:
            self._pgid = os.getpgid(pid)
        except OSError:
            self._pgid = None

    def _get_all_pids(self) -> List[int]:
        """获取当前需要监控的所有进程 PID（进程组或单进程）。"""
        if self._pgid is not None:
            return _get_process_group_pids(self._pgid)
        elif os.path.exists(f"/proc/{self.pid}"):
            return [self.pid]
        return []

    def snapshot(self) -> ProcStats:
        stats = ProcStats(main_pid=self.pid, pgid=self._pgid)

        current_pids = self._get_all_pids()
        if not current_pids:
            self._last_wall = time.time()
            self._last_cpu_by_pid = {}
            self._last_pids = []
            return stats

        # 累加所有进程的 CPU 和 RSS
        total_utime = 0
        total_stime = 0
        total_threads = 0
        total_rss = 0
        any_alive = False
        proc_states = Counter()
        thread_states = Counter()
        cpu_by_pid: Dict[int, int] = {}

        for pid in current_pids:
            try:
                utime, stime, num_threads, state = _read_proc_stat(pid)
                total_utime += utime
                total_stime += stime
                total_threads += num_threads
                total_rss += _read_proc_status_rss(pid)
                proc_states[state] += 1
                thread_states.update(_read_thread_states(pid))
                cpu_by_pid[pid] = utime + stime
                any_alive = True
            except Exception:
                continue

        if not any_alive:
            self._last_wall = time.time()
            self._last_cpu_by_pid = {}
            self._last_pids = []
            return stats

        stats.alive = True
        stats.utime_ticks = total_utime
        stats.stime_ticks = total_stime
        stats.num_cpu_ticks = total_utime + total_stime
        stats.threads = total_threads
        stats.process_count = len(cpu_by_pid)
        stats.proc_states = proc_states
        stats.thread_states = thread_states
        stats.rss_bytes = total_rss

        # 使用单核 CPU 百分比计算：d_cpu ticks / (d_wall * CLK_TICK) * 100
        # 这样可以正确显示多核并行时 >100% 的情况
        now = time.time()
        if self._last_wall is not None and self._last_cpu_by_pid:
            d_wall = max(now - self._last_wall, 1e-6)
            d_cpu = 0
            for pid, cpu_ticks in cpu_by_pid.items():
                prev = self._last_cpu_by_pid.get(pid)
                if prev is not None and cpu_ticks >= prev:
                    d_cpu += cpu_ticks - prev
            stats.cpu_pct = (d_cpu / _CLK_TICK) / d_wall * 100.0

        self._last_wall = now
        self._last_cpu_by_pid = cpu_by_pid
        self._last_pids = current_pids
        return stats


# ------------------------------ 观测信号分类 ------------------------------

HEALTH_RUNNING = "alive + recent logs"
HEALTH_BUSY_QUIET = "alive + host busy"
HEALTH_IDLE_QUIET = "alive + logs quiet"
HEALTH_STALL = "alive + no recent evidence"
HEALTH_ZOMBIE = "process exited"

_HEALTH_COLOR = {
    HEALTH_RUNNING: GREEN,
    HEALTH_BUSY_QUIET: CYAN,
    HEALTH_IDLE_QUIET: YELLOW,
    HEALTH_STALL: RED,
    HEALTH_ZOMBIE: RED,
}


def classify_health(
    stats: ProcStats,
    log_lag_s: float,
    phase_ttl_s: float,
) -> str:
    """根据 /proc 指标和日志停滞时长给出弱观测信号。

    这些值只描述外部观测事实，不代表 simulator 内部进展或死锁诊断。
    """
    if not stats.alive:
        return HEALTH_ZOMBIE

    if log_lag_s < 5.0:
        return HEALTH_RUNNING
    if stats.cpu_pct >= 20.0:
        return HEALTH_BUSY_QUIET
    if stats.cpu_pct < 1.0 and log_lag_s >= 60.0 and phase_ttl_s >= 120.0:
        return HEALTH_STALL
    return HEALTH_IDLE_QUIET


# ------------------------------ Dashboard ------------------------------

def _format_elapsed(s: float) -> str:
    m, sec = divmod(s, 60)
    if m >= 60:
        h, m = divmod(int(m), 60)
        return f"{h:02d}:{int(m):02d}:{int(sec):02d}"
    return f"{int(m):02d}:{sec:04.1f}"


class Dashboard:
    """面板 + 事件流渲染器。

    使用模式：
        dash = Dashboard(pid, log_path, title, start_time)
        dash.start()                 # 启动内部定时线程
        ...                          # 业务过程中可随时 dash.push_event(...)
        dash.stop()                  # 停止线程，落末帧，返回事件/告警汇总

    并发：
        内部起一个守护线程做 0.5s 一次的采样与刷新；stop() 通过 Event 通知退出。
        外部只应通过 ``push_event`` / ``feed_log_line`` 与本对象交互，渲染细节不暴露。
    """

    REFRESH_INTERVAL_S = 0.5
    NON_TTY_HEARTBEAT_S = 30.0
    RECENT_EVENTS_N = 5
    LOG_WINDOW_S = 10.0
    FINAL_SETTLE_TIMEOUT_S = 1.0
    FINAL_SETTLE_INTERVAL_S = 0.1

    def __init__(
        self,
        pid: int,
        log_path: str,
        title: str,
        start_time: float,
        is_tty: Optional[bool] = None,
        process_alive_cb: Optional[Callable[[], bool]] = None,
    ):
        self.pid = pid
        self.log_path = log_path
        self.title = title
        self.start_time = start_time
        self._is_tty = _IS_TTY if is_tty is None else is_tty
        self._process_alive = process_alive_cb or (lambda: True)

        self.detector = PhaseDetector()
        self.monitor = ProcessMonitor(pid)

        self._events: List[Event] = []
        self._recent: "deque[Event]" = deque(maxlen=self.RECENT_EVENTS_N)
        self._events_lock = threading.Lock()

        self._last_log_mtime: float = time.time()
        self._last_stats = ProcStats()
        self._log_lines_total = 0
        self._log_line_times: "deque[float]" = deque()
        self._log_lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._panel_drawn_lines = 0  # 已经绘制的面板行数（用于 cursor 上移）
        self._last_heartbeat = time.time()
        self._stall_announced = False

    # --------------- 生命周期 ---------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        self._settle_final_snapshot()
        # 确保面板结尾换行干净
        if self._is_tty and self._panel_drawn_lines > 0:
            sys.stdout.write("\n")
            sys.stdout.flush()

    # --------------- 外部输入 ---------------

    def feed_log_line(self, line: str) -> None:
        """来自 reader 线程的单行日志：喂给 phase detector，并产出事件。"""
        now = time.time()
        with self._log_lock:
            self._log_lines_total += 1
            self._last_log_mtime = now
            self._log_line_times.append(now)
            self._prune_log_window_locked(now)
        events = self.detector.feed(line)
        for level, msg in events:
            self.push_event(level, msg)

    def push_event(self, level: str, message: str) -> None:
        ev = Event(
            elapsed_s=time.time() - self.start_time,
            level=level,
            message=message,
        )
        with self._events_lock:
            self._events.append(ev)
            self._recent.append(ev)
        # 非 TTY：事件立即 append 输出；TTY 则由下一次 _refresh 统一绘制。
        if not self._is_tty:
            self._print_event_non_tty(ev)

    # --------------- 查询 ---------------

    @property
    def events(self) -> List[Event]:
        with self._events_lock:
            return list(self._events)

    @property
    def warnings_summary(self) -> List[Tuple[str, int]]:
        return self.detector.warnings_summary()

    # --------------- 主循环 ---------------

    def _run_loop(self) -> None:
        try:
            while not self._stop_evt.is_set():
                if not self._process_alive():
                    self._tick()
                    break
                self._tick()
                self._stop_evt.wait(self.REFRESH_INTERVAL_S)
        except Exception as e:  # pragma: no cover
            # 面板异常不能影响主流程；打一行 debug 后静默退出。
            sys.stderr.write(f"[npusim dashboard error] {e}\n")

    def _tick(self) -> None:
        self._last_stats = self.monitor.snapshot()
        health = self._current_health()

        if health == HEALTH_STALL and not self._stall_announced:
            self._announce_stall(self._last_stats)
            self._stall_announced = True
        elif health != HEALTH_STALL:
            self._stall_announced = False

        if self._is_tty:
            self._refresh_panel(health)
        else:
            self._maybe_heartbeat(health)

    def _current_health(self) -> str:
        log_lag = time.time() - self._last_log_mtime
        phase_ttl = self.detector.phase_ttl()
        return classify_health(self._last_stats, log_lag, phase_ttl)

    def _settle_final_snapshot(self) -> None:
        """结束前短暂等待僵尸进程被回收，并刷新最终进程组状态。"""
        deadline = time.time() + self.FINAL_SETTLE_TIMEOUT_S
        while True:
            self._last_stats = self.monitor.snapshot()
            stats = self._last_stats
            zombie_count = (
                stats.proc_states.get("Z", 0) + stats.thread_states.get("Z", 0)
            )
            if not stats.alive or stats.process_count == 0 or zombie_count == 0:
                break
            now = time.time()
            if now >= deadline:
                break
            time.sleep(min(self.FINAL_SETTLE_INTERVAL_S, deadline - now))

        if self._is_tty and self._panel_drawn_lines > 0:
            self._refresh_panel(self._current_health())

    def _prune_log_window_locked(self, now: float) -> None:
        cutoff = now - self.LOG_WINDOW_S
        while self._log_line_times and self._log_line_times[0] < cutoff:
            self._log_line_times.popleft()

    def _recent_log_lines(self) -> int:
        now = time.time()
        with self._log_lock:
            self._prune_log_window_locked(now)
            return len(self._log_line_times)

    # --------------- 非 TTY 输出 ---------------

    def _print_event_non_tty(self, ev: Event) -> None:
        tag_map = {
            EVENT_PHASE: "phase",
            EVENT_INFO: "info",
            EVENT_WARN: "warn",
            EVENT_USER: "user",
            EVENT_HEALTH: "health",
            EVENT_ERROR: "error",
        }
        tag = tag_map.get(ev.level, ev.level)
        prefix = (
            USER_PREFIX + " "
            if ev.level == EVENT_USER
            else ""
        )
        sys.stdout.write(
            f"[npusim {_format_elapsed(ev.elapsed_s)}] "
            f"[{tag}] {prefix}{ev.message}\n"
        )
        sys.stdout.flush()

    def _maybe_heartbeat(self, health: str) -> None:
        if time.time() - self._last_heartbeat < self.NON_TTY_HEARTBEAT_S:
            return
        stats = self._last_stats
        sys.stdout.write(
            f"[npusim {_format_elapsed(time.time() - self.start_time)}] "
            f"[heartbeat] signal={health} "
            f"phase={self.detector.phase or '-'} "
            f"pgid={stats.pgid or '-'} processes={stats.process_count} "
            f"threads={stats.threads} cpu_cores={stats.cpu_pct / 100.0:.1f} "
            f"resident={stats.rss_hr()} log_lines={self._log_lines_total}\n"
        )
        sys.stdout.flush()
        self._last_heartbeat = time.time()

    # --------------- TTY 面板渲染 ---------------

    def _refresh_panel(self, health: str) -> None:
        lines = self._render_panel_lines(health)

        term_w = shutil.get_terminal_size((100, 24)).columns
        out = []
        # 上移覆盖旧面板（首帧不上移）
        if self._panel_drawn_lines > 0:
            out.append(f"\033[{self._panel_drawn_lines}A")
        for line in lines:
            truncated = _truncate_visible(line, term_w - 1)
            out.append(f"{truncated}\033[K\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._panel_drawn_lines = len(lines)

    def _render_panel_lines(self, health: str) -> List[str]:
        stats = self._last_stats
        elapsed = _format_elapsed(time.time() - self.start_time)
        phase_label = PHASE_LABELS.get(self.detector.phase or "", "(waiting)")
        progress = self.detector.progress_label()
        dot = _c(DOT_CHAR, _HEALTH_COLOR.get(health, WHITE))
        title_line = f" npusim record {ARROW} {self.title} "

        width = max(72, min(shutil.get_terminal_size((100, 24)).columns - 2, 110))
        inner = width - 2

        def _bar(left: str, right: str) -> str:
            """构造 ┌─ left ─[填充]─ right ─┐ 风格的分隔行"""
            left_s = f"{BOX_TL}{BOX_H}{left}{BOX_H}"
            right_s = f"{BOX_H}{right}{BOX_H}{BOX_TR}"
            pad = max(inner - (len(left_s) - 2) - (len(right_s) - 2), 1)
            return left_s + BOX_H * pad + right_s

        def _row(content: str) -> str:
            """单行内容 │ content │，用 _pad_visible 把可见长度凑到 inner 字符。"""
            padded = _pad_visible(content, inner - 2)
            return f"{BOX_V} {padded} {BOX_V}"

        def _divider() -> str:
            return f"{BOX_LT}{BOX_H * inner}{BOX_RT}"

        def _divider_labeled(label: str) -> str:
            label_s = f"{BOX_H} {label} "
            rest = max(inner - len(label_s), 0)
            return f"{BOX_LT}{label_s}{BOX_H * rest}{BOX_RT}"

        lines: List[str] = []
        # 第 1 行：带标题的顶边
        header_label = title_line
        fill = max(inner - len(header_label) - 2, 0)
        lines.append(
            f"{BOX_TL}{BOX_H}{_c(header_label, BOLD + WHITE)}{BOX_H * fill}{BOX_TR}"
        )
        # 第 2 行：elapsed / health / phase / progress
        status_line = (
            f"{elapsed}   {dot} signal: {_c(health, _HEALTH_COLOR.get(health, WHITE))}"
            f"   inferred phase: {_c(phase_label, BOLD)}"
        )
        if progress:
            status_line += f"   {progress}"
        lines.append(_row(status_line))
        lines.append(_divider())
        # 第 4~8 行：runtime 指标
        now = time.time()
        log_lag = now - self._last_log_mtime
        recent_log_lines = self._recent_log_lines()
        lines.append(
            _row(
                f"process group     pgid {_fmt_optional_int(stats.pgid)}   "
                f"main pid {stats.main_pid or '-'}   "
                f"processes {stats.process_count}   threads {stats.threads}"
            )
        )
        lines.append(
            _row(
                f"host              cpu {stats.cpu_pct / 100.0:4.1f} cores   "
                f"memory resident {stats.rss_hr()}"
            )
        )
        lines.append(_row(f"proc states       {_format_state_counts(stats.proc_states)}"))
        lines.append(_row(f"thread states     {_format_state_counts(stats.thread_states)}"))
        lines.append(
            _row(
                f"npusim.log        last line {log_lag:5.1f}s ago   "
                f"recent {recent_log_lines} lines / {int(self.LOG_WINDOW_S)}s"
            )
        )
        lines.append(_divider_labeled("recent events"))
        # Recent events (最多 RECENT_EVENTS_N 条；不足时补空行，保持面板固定高度)
        with self._events_lock:
            recent = list(self._recent)
        for i in range(self.RECENT_EVENTS_N):
            if i < len(recent):
                ev = recent[i]
                lines.append(_row(_format_event_short(ev)))
            else:
                lines.append(_row(""))
        lines.append(f"{BOX_BL}{BOX_H * inner}{BOX_BR}")
        return lines

    # --------------- 其他事件 ---------------

    def _announce_stall(self, stats: ProcStats) -> None:
        """长时间缺少外部进展证据时，追加可保留的排查提示。"""
        self.push_event(
            EVENT_HEALTH,
            f"NO RECENT EVIDENCE: no log lines for "
            f"{time.time() - self._last_log_mtime:.0f}s  "
            f"cpu={stats.cpu_pct / 100.0:.1f} cores  resident={stats.rss_hr()}  "
            f"phase_ttl={self.detector.phase_ttl():.0f}s",
        )
        self.push_event(
            EVENT_HEALTH,
            f"  hint: tail -F {self.log_path}",
        )
        self.push_event(
            EVENT_HEALTH,
            f"  hint: top -H -p {self.pid}",
        )
        if stats.pgid is not None:
            self.push_event(
                EVENT_HEALTH,
                f"  hint: ps -o pid,ppid,pgid,stat,comm -g {stats.pgid}",
            )
        self.push_event(
            EVENT_HEALTH,
            "  hint: Ctrl-C to abort; npusim will archive partial assets.",
        )


# ------------------------------ 辅助格式化 ------------------------------

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")

_PROC_STATE_LABELS = {
    "R": "running",
    "S": "sleeping",
    "D": "uninterruptible",
    "Z": "zombie",
    "T": "stopped",
    "t": "tracing-stop",
    "I": "idle",
    "X": "dead",
    "x": "dead",
    "K": "wakekill",
    "W": "waking",
    "P": "parked",
}


def _fmt_optional_int(value: Optional[int]) -> str:
    return str(value) if value is not None else "-"


def _format_state_counts(states: Counter) -> str:
    if not states:
        return "none observed"

    parts = []
    ordered_states = list(_PROC_STATE_LABELS)
    ordered_states.extend(
        sorted(s for s in states if s not in _PROC_STATE_LABELS)
    )
    for state in ordered_states:
        count = states.get(state, 0)
        if count <= 0:
            continue
        label = _PROC_STATE_LABELS.get(state, f"unknown({state})")
        parts.append(f"{label} {count}")
    return "   ".join(parts)


def _visible_len(s: str) -> int:
    """去掉 ANSI 控制字符后计算字符长度，用于面板对齐。"""
    return len(_ANSI_RE.sub("", s))


def _pad_visible(s: str, width: int) -> str:
    """保持 ANSI 颜色不被切断的左对齐 pad 到指定可见宽度；超宽则截断。"""
    vis = _visible_len(s)
    if vis >= width:
        return _truncate_visible(s, width)
    return s + " " * (width - vis)


def _truncate_visible(s: str, width: int) -> str:
    """按可见字符截断，保留已经出现的 ANSI 颜色序列，不破坏颜色闭合。"""
    if _visible_len(s) <= width:
        return s
    # 简化实现：直接按字符逐个累加，遇到 ANSI 段不计入可见长度
    out = []
    count = 0
    i = 0
    while i < len(s) and count < width:
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group(0))
            i = m.end()
            continue
        out.append(s[i])
        count += 1
        i += 1
    # 结尾补 reset，防止颜色穿透到下一行
    out.append(RESET)
    return "".join(out)


def _format_event_short(ev: Event) -> str:
    """把 Event 渲染成 recent events 区的一行文本（含 ANSI 着色）。"""
    ts = _format_elapsed(ev.elapsed_s)
    if ev.level == EVENT_USER:
        return f"{_c(ts, GREY)}  {_c(USER_PREFIX + ' ' + ev.message, MAGENTA)}"
    if ev.level == EVENT_PHASE:
        return f"{_c(ts, GREY)}  {_c(ev.message, CYAN)}"
    if ev.level == EVENT_WARN:
        return f"{_c(ts, GREY)}  {_c('! ' + ev.message, YELLOW)}"
    if ev.level == EVENT_ERROR:
        return f"{_c(ts, GREY)}  {_c('× ' + ev.message, RED)}"
    if ev.level == EVENT_HEALTH:
        return f"{_c(ts, GREY)}  {_c(ev.message, RED)}"
    return f"{_c(ts, GREY)}  {ev.message}"


__all__ = [
    "Dashboard",
    "PhaseDetector",
    "ProcessMonitor",
    "classify_health",
    "is_user_stdout_line",
    "HEALTH_RUNNING",
    "HEALTH_BUSY_QUIET",
    "HEALTH_IDLE_QUIET",
    "HEALTH_STALL",
    "HEALTH_ZOMBIE",
    "PHASE_LABELS",
    "EVENT_PHASE",
    "EVENT_INFO",
    "EVENT_WARN",
    "EVENT_USER",
    "EVENT_HEALTH",
    "EVENT_ERROR",
]
