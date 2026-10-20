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

"""
cannsim record 用户可在无卡环境下执行仿真
"""

import json
import os
import shlex
import sys
from pathlib import Path
from time import time, sleep
from typing import List, Optional

from npusim.core.public.comm import (
    print_error,
    print_info,
    print_warn,
)
from npusim.core.public.dashboard import Dashboard
from npusim.core.public.soc_info import (
    SOC_INFO,
    get_ai_core_count,
    parse_core_id,
)
from npusim.core.record.artifacts import archive_assets, archive_instr_bin_glob, cleanup_internal_dirs
from npusim.core.record.auto_report import generate_auto_report
from npusim.core.record.config import (
    RecordConfig,
    build_record_config_from_args,
    normalize_gen_report,
    preprocess_gen_report_argv,
)
from npusim.core.record.launch_spec import build_launch_spec
from npusim.core.record.paths import RecordPathError, resolve_record_paths
from npusim.core.record.presentation import (
    print_record_summary,
    print_stage3_preamble,
    print_stage_fail,
    print_stage_ok,
    print_start_banner,
)
from npusim.core.record.runner import run_user_app_with_dashboard
from npusim.core.record.runtime import (
    CAMODEL_ASSETS as RECORD_CAMODEL_ASSETS,
    CC_MAX_CHIP,
    RUNTIME_DIRS as RECORD_RUNTIME_DIRS,
    build_execution_env,
    cc_chip_of,
    cc_restore_hccl,
    cc_swap_hccl,
    prepare_runtime_dirs,
)


def _maybe_sleep(seconds: float) -> None:
    """Stage 之间的视觉停顿；NPUSIM_NO_DELAY=1 时跳过，便于 CI / 自动化。"""
    if os.environ.get("NPUSIM_NO_DELAY") == "1" or os.environ.get("CANNSIM_NO_DELAY") == "1":
        return
    sleep(seconds)


# ------------------------------ CannsimRecord 流程 ------------------------------


class CannsimRecord:
    # 仿真模型仍写在子进程 ``cwd`` 或启动目标相关目录下、且不能被环境变量
    # 直接重定向的产物。``npusim.log`` 直接写入 ``target_dir``；``log_ca/`` 通过
    # ``CAMODEL_LOG_PATH`` / ``STARS_LOG_PATH`` 重定向到 ``target_dir`` 下，无需事后搬运。
    # instr 类产物（instr.bin / chip*_instr.bin）不受 env 控制，仍落在子进程 cwd，
    # 由 archive_instr_bin_glob 按后缀 glob 搬运，覆盖单 chip 和多 chip 场景。
    CAMODEL_ASSETS: tuple = RECORD_CAMODEL_ASSETS

    # 运行时需要在 target_dir 下创建的子目录
    RUNTIME_DIRS: tuple = RECORD_RUNTIME_DIRS

    def __init__(self, cfg: RecordConfig):
        self.launch = build_launch_spec(cfg.user_app, cfg.user_option, Path.cwd())
        self.output = Path(cfg.output).resolve()
        self.origin_soc_version = cfg.soc_version
        self.soc_version = cfg.soc_version
        self.gen_report = cfg.gen_report
        self.user_option = cfg.user_option
        self.object_file = cfg.object_file
        self.core_id = cfg.core_id or ""
        self.model = cfg.model
        self.chip = int(getattr(cfg, "chip", 0) or 0)
        self.cc_chip = 1
        self.soc_version_mapping = SOC_INFO

        # stage1 校验通过后回填：
        # - ai_core_count: 该 SoC 的 AI core 总数（= CORE_ENABLE_MASK 有效位宽）。
        # - resolved_cores: core_id 解析后的核索引元组；None 表示用户未指定，
        #   对应 "保持 model 默认 / 不注入 CORE_ENABLE_MASK" 语义。
        self.ai_core_count: int = 0
        self.resolved_cores: Optional[tuple] = None

        self.cannsim_path = None
        self.ascend_toolkit_path = None
        self.ca_model_path = None
        self.target_dir: Optional[Path] = None
        self.record_dir: Optional[Path] = None
        self.cannsim_log: Optional[Path] = None

        # Stage 1/2 成功路径下被压缩掉的诊断信息；失败路径才完整打出。
        self._stage1_context: dict = {}
        self._stage2_context: dict = {}
        # 汇总块使用：只在 summary 统一打一次
        self._last_dashboard: Optional[Dashboard] = None
        self._last_stderr_tail: str = ""
        self._last_returncode: Optional[int] = None
        self._last_run_elapsed: float = 0.0
        self._interrupted: bool = False
        self._execution_argv: list = []
        self._execution_cwd: Path = Path.cwd()

    # --------------- Stage 1: Preflight ---------------

    def stage1_parameter_check(self) -> bool:
        """压缩版：成功只输出一行 OK；失败则把完整的参数表作为诊断上下文打出。

        设计意图:  stage1 的价值在"失败时的明确报错"，成功路径下用户
        刚敲完命令行不需要再被一张表反刍一次。
        """
        params = {
            "USER_APP": self.launch.raw,
            "launch-kind": self.launch.kind,
            "archive-label": self.launch.label,
            "soc-version": f"{self.origin_soc_version}",
            "gen-report": str(self.gen_report),
            "output": str(self.output),
            "user-option": self.user_option if self.user_option else "(none)",
            "core-id": self.core_id if self.core_id else "(default)",
            "model": self.model,
        }
        self._stage1_context = params

        if not self.launch.raw:
            return self._stage_fail(1, "Missing required parameter: user_app")
        if not self.output:
            return self._stage_fail(1, "Missing required option: output")
        if not self.soc_version:
            return self._stage_fail(1, "Missing required option: soc-version")

        # 命令模式（如 "python xxx", "bash ./run.sh"）不要求 user_app 是本地文件。
        # 本地文件模式必须验证路径存在且为普通文件，保持二进制场景的明确报错。
        if self.launch.kind in ("binary", "script"):
            executable_path = self.launch.executable_path
            if executable_path is None or not executable_path.exists():
                return self._stage_fail(1, f"User application does not exist: {executable_path}")
            if not executable_path.is_file():
                return self._stage_fail(1, f"User application is not a file: {executable_path}")

        if self.soc_version not in self.soc_version_mapping.keys():
            supported = list(self.soc_version_mapping.keys())
            return self._stage_fail(
                1,
                f"soc-version '{self.soc_version}' is not supported. Supported: {supported}",
            )

        # 与 soc_info 对齐的防呆检测：core-id 必须落在该 SoC 的 AI core 范围内。
        try:
            self.ai_core_count = get_ai_core_count(self.origin_soc_version)
        except ValueError as e:
            return self._stage_fail(1, str(e))

        if self.core_id:
            try:
                self.resolved_cores = parse_core_id(
                    self.core_id,
                    self.ai_core_count,
                    default=(0,),
                )
            except ValueError as e:
                return self._stage_fail(
                    1,
                    f"Invalid --core-id '{self.core_id}' for {self.origin_soc_version} "
                    f"(AI cores = {self.ai_core_count}): {e}",
                )
        else:
            # 用户未指定：保持 model 默认（不注入 CORE_ENABLE_MASK）；
            # 但 -g 触发的 auto-report 仍按历史约定默认落到核 0。
            self.resolved_cores = None

        self.soc_version = self.origin_soc_version
        extra = f"USER_APP={self.launch.label} SoC={self.origin_soc_version}"
        if self.resolved_cores is not None:
            extra += f" cores={self._format_cores(self.resolved_cores)}"
        self._stage_ok(1, "Preflight", extra=extra)
        return True

    @staticmethod
    def _format_cores(cores: tuple) -> str:
        """把核心索引元组折叠回紧凑字符串，用于日志展示（如 (0,1,2,5) -> '0-2,5'）。"""
        if not cores:
            return "(none)"
        sorted_cores = sorted(set(cores))
        ranges = []
        lo = prev = sorted_cores[0]
        for c in sorted_cores[1:]:
            if c == prev + 1:
                prev = c
                continue
            ranges.append(f"{lo}" if lo == prev else f"{lo}-{prev}")
            lo = prev = c
        ranges.append(f"{lo}" if lo == prev else f"{lo}-{prev}")
        return ",".join(ranges)

    # --------------- Stage 2: Path definition ---------------

    def stage2_define_paths(self) -> bool:
        """解析 Ascend toolkit / CAModel / 归档目录路径。

        成功路径下只打一行 OK；Archive/log 在 summary 统一展示。
        """
        try:
            paths, context = resolve_record_paths(self.output, self.soc_version, self.launch)
        except RecordPathError as e:
            self._stage2_context = e.context
            return self._stage_fail(2, str(e))

        self._stage2_context = context
        self.ascend_toolkit_path = paths.ascend_toolkit_path
        self.ca_model_path = paths.ca_model_path
        self.target_dir = paths.target_dir
        self.record_dir = paths.record_dir
        self.cannsim_log = paths.cannsim_log

        self._stage_ok(2, "Paths resolved")
        return True

    # --------------- Stage 3: Run ---------------

    def stage3_execute(self) -> bool:
        try:
            (self.record_dir / ".soc-version").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "soc_version": self.origin_soc_version,
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            print_error(f"Failed to write .soc-version under {self.record_dir}: {e}")
            return False

        # 记录用户可执行文件的绝对路径，供 `npusim report` 在未显式指定
        # --object-file 时自动回退用于 PC→源码行映射（见 report.py 的自动回退逻辑）。
        # 仅本地文件模式（binary/script）记录；command 模式（如 "python xxx"）
        # 无单一可执行 ELF，留空由用户自行指定 --object-file。
        self._write_user_app_marker()

        argv = self.launch.argv
        app_cwd = self.launch.cwd
        asset_roots = list(self.launch.asset_roots)
        self._execution_argv = argv
        self._execution_cwd = app_cwd

        try:
            if not self._create_runtime_env(app_cwd, asset_roots):
                return False

            self._print_stage3_preamble()

            if self.resolved_cores:
                print_info(f"(cores={self._format_cores(self.resolved_cores)}, ai_cores={self.ai_core_count})")

            if not self._execute_user_app(argv, app_cwd, self.launch.is_python):
                archive_assets(self.record_dir, self.CAMODEL_ASSETS, asset_roots, on_fail_delete=False)
                archive_instr_bin_glob(self.record_dir, asset_roots)
                return False

            if not archive_assets(self.record_dir, self.CAMODEL_ASSETS, asset_roots, on_fail_delete=False):
                return False

            if not archive_instr_bin_glob(self.record_dir, asset_roots):
                return False

            return True

        except Exception as e:
            print_error(f"Error during execution: {e}")
            archive_assets(self.record_dir, self.CAMODEL_ASSETS, asset_roots, on_fail_delete=True)
            return False

    # --------------- 运行时目录 & USER_APP 执行 ---------------

    def _write_user_app_marker(self) -> None:
        """把用户可执行文件的绝对路径写进 record 目录下的 ``.user-app``。

        供 ``npusim report`` 在未显式指定 ``--object-file`` 时自动回退。仅本地文件
        模式且文件确实存在时写入；script 模式不一定是单一可 ELF 产物，不写入。
        command 模式（如 ``python xxx``）或路径已失效时也不写，留由用户自行指定。
        写入失败仅告警，不影响 record。
        """
        marker = self.record_dir / ".user-app"
        try:
            exe = self.launch.executable_path
            if self.launch.kind != "binary" or not exe:
                return
            try:
                resolved = exe.resolve(strict=True)
            except (OSError, ValueError):
                # 文件不存在或无法解析，静默跳过（不阻塞 record）
                return
            marker.write_text(str(resolved) + "\n", encoding="utf-8")
        except Exception as e:
            print_warn(f"Failed to write .user-app marker under {self.record_dir}: {e}")

    def _create_runtime_env(self, app_cwd: Path, asset_roots: List[Path]) -> bool:
        """创建仿真模型所需的运行时子目录。

        模型当前的产物分布：

        - ``log/`` / ``log/ub_log/`` / ``run_log/``：模型直接往子进程 ``cwd`` 下的同名
          目录写，目录必须预先存在。这部分仍建在 ``app_cwd``，运行结束后由
          ``archive_assets`` 搬到 ``target_dir``。
        - ``log_ca/``：通过 ``CAMODEL_LOG_PATH`` / ``STARS_LOG_PATH`` 重定向到
          ``target_dir/log_ca``，无需事后搬运，但模型同样不会自己 mkdir，所以提前建好。
        - ``instr.bin`` / ``chip*_instr.bin``：不受 env 控制，仍落在子进程 ``cwd``，
          运行结束后由 ``archive_instr_bin_glob`` 按后缀 glob 从 ``asset_roots`` 候选
          搬到 ``record_dir``，覆盖单 chip 和多 chip 产物。

        ``asset_roots`` 仅在失败路径上作为 fallback 候选传给 ``archive_assets``。
        """
        if not prepare_runtime_dirs(app_cwd, self.target_dir):
            archive_assets(self.record_dir, self.CAMODEL_ASSETS, asset_roots, on_fail_delete=True)
            archive_instr_bin_glob(self.record_dir, asset_roots)
            return False
        return True

    def _execute_user_app(self, argv: list, app_cwd: Path, is_python_launch: bool) -> bool:
        """拉起 USER_APP 子进程并显示动态仪表板。

        执行策略（由调用方 stage3_execute 决定并传入）：
        - Shell 命令（如 "python xxx", "bash ./run.sh"）：直接用 bash -c 执行，
          不需要本地文件存在，适合 Python 算子等通过解释器启动的场景。
        - 本地 ELF 二进制：直接 execve 执行，最高效。
        - 本地脚本/非 ELF 文件（即使没有 shebang）：用 bash 执行，
          避免用户忘记写 shebang 导致失败。

        接 SIGINT：KeyboardInterrupt 时标记 self._interrupted=True，
        调用方 record() 负责打中断提示并归档已产出的部分资产。
        """
        # Multi-device CC chip count: --chip overrides env; otherwise use $CAMODEL_SIM_RANK_SIZE.
        if self.chip and self.chip > 0:
            os.environ["CAMODEL_SIM_RANK_SIZE"] = str(self.chip)
            self.cc_chip = self.chip
        else:
            self.cc_chip = cc_chip_of(os.environ)

        if self.cc_chip > CC_MAX_CHIP:
            print_error(
                f"CC chip count {self.cc_chip} exceeds the max supported {CC_MAX_CHIP} "
                "(HcclCombinOpParam950 windowsIn capacity); "
                "reduce --chip / CAMODEL_SIM_RANK_SIZE."
            )
            return False

        try:
            env = build_execution_env(
                os.environ,
                self.ca_model_path,
                self.record_dir,
                self.origin_soc_version,
                self.resolved_cores,
                is_python_launch,
                object_file=self.object_file,
                model=self.model,
                cc_chip=self.cc_chip,
            )
        except RecordPathError as e:
            print_error(str(e))
            return False

        # npusim.log 直接写入 archive，避免与子进程 cwd 绑定。
        log_path = str(self.cannsim_log)
        print()
        start_t = time()

        # Temporarily point the real-CANN libhccl_fwk.so at the CC mock so the op-host
        # path-based dlopen hits it; ALWAYS restored (even on interrupt/exception).
        hccl_handle = None
        if self.cc_chip > 1:
            hccl_handle = cc_swap_hccl(env, self.origin_soc_version)
        try:
            try:
                cmdline = " ".join(shlex.quote(x) for x in argv)
                result = run_user_app_with_dashboard(
                    argv,
                    env=env,
                    cwd=str(app_cwd),
                    log_path=log_path,
                    title=f"{cmdline} · {self.origin_soc_version}",
                )
            except KeyboardInterrupt:
                self._interrupted = True
                self._last_run_elapsed = time() - start_t
                print_warn(f"Interrupted by user after {self._last_run_elapsed:.1f}s; partial assets will be archived.")
                return False

            self._last_dashboard = result.dashboard
            self._last_stderr_tail = result.stderr_tail
            self._last_returncode = result.returncode
            self._interrupted = result.interrupted
            self._last_run_elapsed = time() - start_t

            if result.interrupted:
                print_warn(f"Interrupted by user after {self._last_run_elapsed:.1f}s; partial assets will be archived.")
                return False

            if result.success:
                return True

            return False
        finally:
            cc_restore_hccl(hccl_handle)

    # --------------- Stage 3 开场白 ---------------

    def _print_stage3_preamble(self) -> None:
        """Stage 3 一次性开场白：标明在仿什么 + Reference benchmarks。

        用户输入参数有值才展示，无值则不展示。
        archive/log 统一在 summary 中展示。
        """
        print_stage3_preamble(
            self._execution_argv,
            self._execution_cwd,
            self.origin_soc_version,
            self.launch.kind,
            self.core_id,
            self.user_option,
            self.gen_report,
        )

    # --------------- 通用 stage 打印 ---------------

    def _stage_ok(self, idx: int, name: str, extra: str = "") -> None:
        """成功路径的极简打印：``[1/3] Preflight ... OK (elapsed)``。"""
        print_stage_ok(idx, name, extra)

    def _stage_fail(self, idx: int, reason: str) -> bool:
        """失败路径：标红 FAIL 并把该 stage 已收集的诊断上下文完整打出。

        Returns: always False （方便在 stage1/2 里直接 ``return self._stage_fail(...)``）。
        """
        ctx = self._stage1_context if idx == 1 else self._stage2_context
        return print_stage_fail(idx, reason, ctx)

    # --------------- 最终 Summary ---------------

    def print_summary(self, success: bool) -> None:
        """Summary 块：Artifacts + Warnings + Next steps，间距紧凑、层次分明。

        布局规则：
        - dash 与 header 之间无空行；header 后紧跟子标题（首行即标题本身）
        - 区块之间以单个空行分隔；区块内 entry 之间无空行
        - Warnings 在有数据时才出现；Next hints 在成功时才出现
        """
        print_record_summary(self, success)

    # --------------- Orchestration ---------------

    def run(self) -> bool:
        _maybe_sleep(0.2)
        if not self.stage1_parameter_check():
            return False

        _maybe_sleep(0.2)
        if not self.stage2_define_paths():
            return False

        _maybe_sleep(0.2)
        print("[3/3] Simulation")
        ok = self.stage3_execute()
        return ok


# ------------------------------ 入口 ------------------------------


def record(cfg: RecordConfig) -> bool:
    """执行 cannsim record 流程，返回执行是否成功。

    该函数不再调用 sys.exit；CLI 层（main.py 或本文件 __main__）负责根据
    返回值设置进程退出码。
    """
    print_start_banner()

    executor = CannsimRecord(cfg)
    success = False

    try:
        try:
            success = executor.run()
        except KeyboardInterrupt:
            # _execute_user_app 里已设了 _interrupted；但以防万一
            executor._interrupted = True
            print_warn("Received KeyboardInterrupt; cleaning up.")
            success = False

        executor.print_summary(success)

        if success and cfg.gen_report:
            generate_auto_report(
                executor.record_dir,
                executor.target_dir,
                cfg.gen_report,
                cfg.core_id,
                executor.object_file,
            )
        return success
    finally:
        cleanup_internal_dirs(executor.record_dir)


_normalize_gen_report = normalize_gen_report
_build_record_config_from_args = build_record_config_from_args


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("user_app", help="Path to the user application")
    parser.add_argument("-o", "--output", default=".", help="Output directory")
    parser.add_argument("-s", "--soc-version", required=True, help="Target chip version")
    parser.add_argument(
        "-g",
        "--gen-report",
        nargs="?",
        const=True,
        default=False,
        help="Generate report. Use '-g vf' to additionally export VF IPC JSON.",
    )
    parser.add_argument("-u", "--user-option", default="", help="User-defined options")
    parser.add_argument(
        "-n",
        "--core-id",
        default="",
        help="AI core(s) whose logs are enabled via CORE_ENABLE_MASK. "
        "Same format as `npusim report -n`: all, 0-2,12-14, 5.",
    )
    parser.add_argument(
        "--pv",
        action="store_true",
        default=False,
        help="Use programmer-view (PV) simulation: functional only, faster, "
        "without accurate timing. If not specified, accurate timing simulation "
        "is used.",
    )

    args = parser.parse_args(preprocess_gen_report_argv(sys.argv[1:]))
    sys.exit(0 if record(_build_record_config_from_args(args)) else 1)
