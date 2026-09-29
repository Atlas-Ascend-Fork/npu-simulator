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

"""Subprocess execution helpers for cannsim record."""

import os
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from time import time
from typing import Optional

from npusim.core.public.comm import print_error, print_info
from npusim.core.public.dashboard import Dashboard


@dataclass
class RunResult:
    """Structured USER_APP execution result."""

    success: bool
    dashboard: Optional[Dashboard] = None
    stderr_tail: str = ""
    interrupted: bool = False
    returncode: Optional[int] = None


def _tail_log(log_path, max_lines=10):
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(deque(f, maxlen=max_lines))
    except Exception:
        return ""


def _terminate_process_group(process, timeout=5.0):
    try:
        pgid = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    except OSError:
        pgid = None

    if pgid is None:
        process.terminate()
    else:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return

    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass

    if pgid is None:
        process.kill()
    else:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_user_app_with_dashboard(argv, env, cwd, log_path, title):
    """拉起 USER_APP 子进程，挂一个 :class:`Dashboard` 做实时监控与渲染。

    行为契约：

    - stdout/stderr tee 到 ``log_path``（npusim.log 保持完整且可追溯）。
    - 读线程每拿到一行日志，都喂给 Dashboard 做阶段识别 / user stdout 抓取。
    - Dashboard 自己起采样线程（0.5s 周期），在 TTY 下原地刷新面板，
      非 TTY 下走事件流 append + 30s 心跳。
    - 进程结束 / 异常中断时，先停 Dashboard 再关闭 tee 文件，确保最后一帧面板清零。
    - SIGINT: 清理整个 USER_APP 进程组，并通过 RunResult.interrupted 回传给调用方。

    Returns:
        :class:`RunResult`，包含 success / interrupted / returncode / dashboard /
        stderr_tail，辅助上层打印 summary。
    """
    start_time = time()

    try:
        tee_fp = open(log_path, "w", encoding="utf-8")
    except Exception as e:
        print_error(f"Failed to open log file {log_path}: {e}")
        return RunResult(success=False, stderr_tail=str(e))

    try:
        process = subprocess.Popen(
            argv,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=cwd,
            bufsize=1,  # 行缓冲，让读线程尽快拿到每一行
            # 创建新会话，使子进程成为会话Leader，PGID=PID，便于 ProcessMonitor 追踪整个进程树
            start_new_session=True,
        )
    except Exception as e:
        tee_fp.close()
        print_error(f"Failed to spawn subprocess: {e}")
        return RunResult(success=False, stderr_tail=str(e))

    tee_lock = threading.Lock()

    dashboard = Dashboard(
        pid=process.pid,
        log_path=log_path,
        title=title,
        start_time=start_time,
        process_alive_cb=lambda: process.poll() is None,
    )

    def read_output(pipe):
        """从子进程 pipe 读行：tee 到 log 文件 + 投递给 dashboard 做语义识别。"""
        try:
            for line in iter(pipe.readline, ""):
                semantic_line = line.rstrip("\r\n")
                with tee_lock:
                    tee_fp.write(line)
                    tee_fp.flush()
                if semantic_line:
                    dashboard.feed_log_line(semantic_line)
        except Exception as e:
            print_error(f"Error reading subprocess output: {e}")
        finally:
            pipe.close()

    stdout_thread = threading.Thread(
        target=read_output, args=(process.stdout,), daemon=True
    )
    stderr_thread = threading.Thread(
        target=read_output, args=(process.stderr,), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    dashboard.start()

    interrupted = False
    try:
        process.wait()
    except KeyboardInterrupt:
        # 透传给上层；上层 record() 负责做 graceful archive 和事件汇总
        interrupted = True
        _terminate_process_group(process)
    finally:
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        dashboard.stop()
        try:
            tee_fp.close()
        except Exception:
            pass

    success = process.returncode == 0
    # 失败时截取最后 10 行作为 stderr_tail，便于在 summary 里帮用户定位错误。
    stderr_tail = _tail_log(log_path) if not success else ""

    elapsed = time() - start_time
    if interrupted:
        print_error(f"USER_APP interrupted after {elapsed:.2f}s")
    elif success:
        print_info(f"USER_APP executed successfully in {elapsed:.2f}s")
    else:
        print_error(f"USER_APP failed after {elapsed:.2f}s (exit={process.returncode})")

    return RunResult(
        success=success,
        dashboard=dashboard,
        stderr_tail=stderr_tail,
        interrupted=interrupted,
        returncode=process.returncode,
    )
