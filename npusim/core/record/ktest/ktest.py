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

"""
ktest mode: 在 camodel 目录下创建 case 软链接并拉起 ./sim 仿真。
通过 ``record -c <case_dir>`` 触发。
"""

import json
import os
import shlex
import shutil
from pathlib import Path
from time import time
from typing import Optional

from npusim.core.public.comm import print_error, print_info, print_warn
from npusim.core.record.artifacts import archive_instr_bin_glob, archive_helper_dbs
from npusim.core.record.auto_report import generate_auto_report
from npusim.core.record.presentation import (
    print_record_summary,
    print_stage_fail,
    print_stage_ok,
    print_start_banner,
)
from npusim.core.record.runner import run_user_app_with_dashboard
from npusim.core.record.ktest.config import KtestConfig
from npusim.core.record.ktest.paths import KtestPathError, resolve_ktest_paths
from npusim.core.record.paths import RecordPathError
from npusim.core.record.ktest.runtime import (
    acquire_camodel_lock,
    build_execution_env,
    create_case_symlink,
    detect_runtime_config,
    ensure_sim_executable,
    prepare_runtime_dirs,
    release_camodel_lock,
    remove_case_symlink,
    resolve_config_template,
)


class CannsimKtest:
    """ktest 三阶段流水线编排器。

    与 ``CannsimRecord`` 结构对齐，但职责更聚焦：

    - Stage 1: 校验 case_dir 存在且为目录；读 top.json 探测 chip 数量并解析模板。
    - Stage 2: 解析 ASCEND_HOME_PATH / camodel / sim 路径，创建归档目录；
    - Stage 3: 建 case 软链 → 预置运行时目录 → 拉起 ./sim（复用 record.runner
      的 Dashboard / npusim.log tee / killpg SIGINT）→ finally 归档全部产物
      到 target_dir 并清理软链。
    """

    def __init__(self, cfg: KtestConfig):
        self.cfg = cfg
        self.case_dir = Path(cfg.case_dir).resolve()
        self.model = cfg.model
        self.config_template: Optional[Path] = None
        self.runtime_config: str = ""
        self.paths = None
        self.target_dir: Optional[Path] = None
        self.record_dir: Optional[Path] = None
        self.cannsim_log: Optional[Path] = None

        self._stage1_context: dict = {}
        self._stage2_context: dict = {}
        self._lock_fp = None
        self._last_dashboard = None
        self._last_stderr_tail: str = ""
        self._last_returncode: Optional[int] = None
        self._last_run_elapsed: float = 0.0
        self._interrupted: bool = False

    # --------------- Stage 1: Preflight ---------------

    def stage1_preflight(self) -> bool:
        params = {
            "case-dir": str(self.case_dir),
            "output": str(self.cfg.output),
        }
        self._stage1_context = params

        if not self.case_dir.exists():
            return self._stage_fail(1, f"case directory does not exist: {self.case_dir}")
        if not self.case_dir.is_dir():
            return self._stage_fail(1, f"case path is not a directory: {self.case_dir}")

        try:
            self.runtime_config = detect_runtime_config(self.case_dir)
        except ValueError as e:
            return self._stage_fail(1, str(e))

        try:
            self.config_template = resolve_config_template(self.runtime_config)
        except ValueError as e:
            return self._stage_fail(1, str(e))

        params["runtime-config"] = self.runtime_config

        extra = f"case={self.case_dir.name} topology={self.runtime_config}"
        self._stage_ok(1, "Preflight", extra=extra)
        return True

    # --------------- Stage 2: Path definition ---------------

    def stage2_resolve_paths(self) -> bool:
        try:
            paths, context = resolve_ktest_paths(self.cfg.output)
        except KtestPathError as e:
            self._stage2_context = e.context
            return self._stage_fail(2, str(e))

        self._stage2_context = context

        self._lock_fp = acquire_camodel_lock(paths.camodel_path)
        if self._lock_fp is None:
            return self._stage_fail(2, "Failed to acquire camodel lock.")

        self.paths = paths
        self.target_dir = paths.target_dir
        self.record_dir = paths.record_dir
        self.cannsim_log = paths.target_dir / "npusim.log"

        self._stage_ok(2, "Paths resolved", extra=f"target={self.target_dir.name}")
        return True

    # --------------- Stage 3: Run ---------------

    def stage3_execute(self) -> bool:
        print("[3/3] Simulation")

        try:
            (self.record_dir / ".soc-version").write_text(
                json.dumps(
                    {"schema_version": 1, "soc_version": "Ascend950"},
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            print_error(f"Failed to write .soc-version under {self.record_dir}: {e}")
            return False

        if not create_case_symlink(self.paths.case_symlink, self.case_dir):
            return False

        try:
            if not prepare_runtime_dirs(self.paths.camodel_path):
                return False

            if not ensure_sim_executable(self.paths.sim_path):
                return False

            try:
                env = build_execution_env(
                    os.environ,
                    self.paths.camodel_path,
                    self.record_dir,
                    self.config_template,
                    model=self.model,
                )
            except RecordPathError as e:
                print_error(str(e))
                return False
            log_path = str(self.cannsim_log)

            argv = [str(self.paths.sim_path)]
            cmdline = " ".join(shlex.quote(x) for x in argv)
            print()
            print(f"  cmd             {cmdline}")
            print(f"  cwd             {self.paths.camodel_path}")
            print(f"  case symlink    {self.paths.case_symlink} -> {self.case_dir}")
            if self.config_template is not None:
                print(f"  CAMODEL_CONFIG_PATH -> {self.config_template} ({self.runtime_config})")
            else:
                print(f"  runtime-config  {self.runtime_config} (no override)")
            print()

            start_t = time()
            result = run_user_app_with_dashboard(
                argv,
                env=env,
                cwd=str(self.paths.camodel_path),
                log_path=log_path,
                title="./sim · dav_3510",
            )

            self._last_dashboard = result.dashboard
            self._last_stderr_tail = result.stderr_tail
            self._last_returncode = result.returncode
            self._interrupted = result.interrupted
            self._last_run_elapsed = time() - start_t

            return result.success

        except KeyboardInterrupt:
            self._interrupted = True
            self._last_run_elapsed = 0.0
            print_warn("Interrupted by user; archiving partial assets.")
            return False
        finally:
            archive_instr_bin_glob(
                self.record_dir,
                [self.paths.camodel_path],
            )
            archive_helper_dbs(
                self.record_dir,
                [self.paths.camodel_path],
            )

            # Clean up runtime dirs created in camodel_path.
            for dirname in ("log", "log_ca", "run_log"):
                dirpath = self.paths.camodel_path / dirname
                if dirpath.exists():
                    try:
                        shutil.rmtree(str(dirpath))
                        print_info(f"Cleaned up {dirname} from {self.paths.camodel_path}")
                    except Exception as e:
                        print_warn(f"Failed to remove {dirpath}: {e}")

            remove_case_symlink(self.paths.case_symlink)

    # --------------- 通用 stage 打印 ---------------

    @staticmethod
    def _stage_ok(idx: int, name: str, extra: str = "") -> None:
        print_stage_ok(idx, name, extra)

    def _stage_fail(self, idx: int, reason: str) -> bool:
        ctx = self._stage1_context if idx == 1 else self._stage2_context
        return print_stage_fail(idx, reason, ctx)

    # --------------- Orchestration ---------------

    def run(self) -> bool:
        if not self.stage1_preflight():
            return False

        if not self.stage2_resolve_paths():
            return False

        return self.stage3_execute()


# ------------------------------ 入口 ------------------------------


def ktest(cfg: KtestConfig) -> bool:
    """执行 ktest 流程，返回执行是否成功。

    通过 ``record -c <case_dir>`` 触发；该函数不再调用 sys.exit；
    CLI 层负责根据返回值设置进程退出码。
    """
    print_start_banner()

    executor = CannsimKtest(cfg)
    success = False

    try:
        try:
            success = executor.run()
        except KeyboardInterrupt:
            executor._interrupted = True
            print_warn("Received KeyboardInterrupt; cleaning up.")
            success = False

        print_record_summary(executor, success)

        if success and cfg.gen_report:
            generate_auto_report(
                record_dir=executor.record_dir,
                target_dir=executor.target_dir,
                gen_report=cfg.gen_report,
                core_id="all",
                object_file="",
            )
        return success
    finally:
        if executor.paths is not None:
            remove_case_symlink(executor.paths.case_symlink)
        release_camodel_lock(executor._lock_fp)
