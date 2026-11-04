# 基于 MoeDistributeDispatchV2 样例的多卡通信仿真（UBMEM 场景）

## 概述

对token数据进行EP（Expert Parallelism）域AllToAllV通信的算子（如`MoeDistributeDispatchV2`、`MoeDistributeCombineV2`），以及AllGatherMatmul、MatmulAllReduce等通算融合（MC2）算子，其单算子调用即涉及多设备通信。NPUSim 支持此类算子的**多卡通信仿真**：在 CPU 仿真环境中拉起 N 张虚拟卡，通信链路由 CANN 包内置的 HCCL mock 库承接，无需真实组网，即可完成功能验证与指令级性能数据采集。

**适用范围**：
- 多卡通信仿真当前仅支持 **UBMEM 场景**（即通信内存位于 UBMEM 的多卡通信），非 UBMEM 场景暂不支持；
- 仅支持**单进程多线程**形态：用户应用程序须为一个进程内按卡开线程（如本文样例每卡一个 `std::thread`），多进程形态暂不支持。

当前支持的仿真芯片：`Ascend950` / `Ascend950DT`（`-s` 指定）；仅支持2p/4p/8p。

本文以 ops-transformer 仓的 `MoeDistributeDispatchV2` 单算子调用用例为例，走通"编译 → 双卡仿真 → 多卡报告"全流程。

## 环境要求

1. 安装 CANN包，并 `source ${install_path}/ascend-toolkit/set_env.sh`。多卡仿真依赖包内三个组件：`tools/simulator/Ascend950PR_9589/camodel`（仿真器）、`tools/simulator/bin/libcannsim_helper.so`（采集库，须与 npusim 版本一致）、`tools/simulator/dav_3510/camodel/libhccl_fwk_camodel.so`（通信 mock 库）。
2. Python 运行时 SQLite >= 3.25（契约 DB 与报告 SQL 使用窗口函数）。检查命令：

   ```bash
   python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
   ```

3. 如需使用本仓最新代码，参考[快速安装指南](../quick_start/quick_install.md)覆盖安装。

## 准备样例

样例来自 ops-transformer 仓 `mc2/moe_distribute_dispatch_v2/examples/`（`test_aclnn_moe_distribute_dispatch_v2.cpp`，A5 路径 `EP_WORLD_SIZE=2`，即双卡）。

**用例修改说明**：用例内 `aclrtSynchronizeStreamWithTimeout` 的超时参数默认按上板速度设置。仿真执行速度远低于上板，同步等待耗时随之变长，超时参数不调大会导致用例在仿真环境下超时失败。使用仿真运行前，请将用例中所有的超时参数调大（本例调整为 `3000000`，单位 ms）：

```cpp
ret = aclrtSynchronizeStreamWithTimeout(args.dispatchV2Stream, 3000000);
```

MC2 算子涉及 HCCL 与多线程，与普通单卡用例相比，CMakeLists.txt 需额外：

```cmake
find_package(Threads REQUIRED)
target_link_libraries(opapi_test PRIVATE
    ${ASCEND_PATH}/lib64/libascendcl.so
    ${ASCEND_PATH}/lib64/libnnopbase.so
    ${ASCEND_PATH}/lib64/libopapi_math.so
    ${ASCEND_PATH}/lib64/libopapi_transformer.so
    ${ASCEND_PATH}/lib64/libc_sec.so
    -Wl,--no-as-needed
    ${ASCEND_PATH}/lib64/libhccl.so
    ${ASCEND_PATH}/lib64/libhccl_fwk.so
    ${CMAKE_THREAD_LIBS_INIT})
```

完整编译方式参考 ops-transformer 仓《编译与运行样例》文档。核心命令：

```bash
source ${install_path}/ascend-toolkit/set_env.sh
cd <CMakeLists.txt 所在目录> && mkdir -p build && cd build
cmake ../ -DCMAKE_CXX_COMPILER=g++ -DCMAKE_SKIP_RPATH=TRUE
make    # 产物: bin/opapi_test
```

## 执行多卡仿真

该用例自身的 `main()` 读取环境变量 `ENV_DEV_NUM` 校验可用卡数（小于 2 直接退出），运行前需导出。随后一条命令完成双卡仿真：

```bash
export ENV_DEV_NUM=2
npusim record ./opapi_test -s Ascend950 -n all --chip 2
```

| 参数 | 说明 |
| --- | --- |
| `--chip 2` | 通信域内仿真卡数；>1 时自动启用多卡通信仿真栈（不指定则读 `$CAMODEL_SIM_RANK_SIZE`，默认 1） |
| `-n all` | 使能全部 AI 核的指令日志（默认仅 core 0） |
| `-s Ascend950` | 仿真芯片版本 |

仿真成功标志（`npusim.log` 与控制台）：

```text
[INFO] device_0 aclnnMoeDistributedispatchV2 and aclnnMoeDistributecombineV2 execute successfully.
[INFO] device_1 aclnnMoeDistributedispatchV2 and aclnnMoeDistributecombineV2 execute successfully.
Simulation SUCCESS · run time 185.5s
```

## 产物解析

多卡采集产物按卡归档，布局如下：

```text
npusim_{timestamp}_opapi_test
├── npusim.log
└── record
    ├── chip0/                            # 0 号卡
    │   ├── 0000_entry_kernel_400.db      # dispatch 阶段 kernel（按 launch 顺序）
    │   └── 0003_entry_kernel_20.db       # combine 阶段 kernel
    ├── chip1/                            # 1 号卡
    │   ├── 0001_entry_kernel_400.db
    │   └── 0002_entry_kernel_20.db
    └── cannsim_helper.log
```

单卡仿真（`--chip 1` 或缺省）不产生 `chip{N}/` 子目录，DB 平铺于 `record/` 下，两套布局 `npusim report` 均能自动识别。

## 生成多卡报告

```bash
npusim report -e npusim_{timestamp}_opapi_test -n all -m
```

`report` 自动检测 `record/chip{N}/` 布局进入多卡模式，逐卡复用单卡报告流水，并支持 `-m` 生成跨核合并流水图：

```text
report
├── chip0_result/                          # 0 号卡报告（results/ 下各 kernel 各核报告）
├── chip1_result/
├── chip0_result/trace_chip0_kernel0.json  # 单卡内跨核合并流水（每 kernel 一份）
├── chip1_result/trace_chip1_kernel1.json
└── trace_allchips.json                    # 全卡全 kernel 全局合并流水
```

合并产物的进程 pid 命名为 `CHIP{c}.KERNEL{k}.CORE{n}.{family}`（如 `CHIP0.KERNEL0.CORE0.AIC`），可直接在 Perfetto（chrome://tracing）中打开 `trace_allchips.json` 观察两卡并行行为。

## 报告查看重点

- **两卡负载均衡**：对比 `CHIP0.*` 与 `CHIP1.*` 各 family 的时间轴覆盖与密度；
- **通信算子占比**：dispatch / combine 两个 kernel 各自的 Task Duration 与计算类 kernel 的比值；
- **同步检测**：`chip{N}_result/sync_instruction_detection_report.txt` 输出卡内同步违例（双卡 32 核全开场景本例为 2212 / 1884 项）。

## 常见问题

**Q：用例一启动就退出，提示检查 ENV_DEV_NUM？**
该样例自身校验可用卡数，`export ENV_DEV_NUM=2`（>= 卡数）后再运行。

**Q：仿真过程中用例报同步等待超时失败？**
仿真速度远低于上板，见"准备样例"中的超时参数修改说明，将 `aclrtSynchronizeStreamWithTimeout` 的超时调大后重跑。

**Q：`--chip` 报 "no camodel template supports chip count N"？**
随包拓扑模板最大 8 卡，更大卡数需自制拓扑模板并通过 `CAMODEL_CONFIG_PATH` 指定。

**Q：仿真中断后 CANN lib64 出现 `.ccbak` 文件 / HCCL 行为异常？**
进程被 SIGKILL 时 HCCL mock 软链未自动恢复。将 `libhccl.so.ccbak` 改回 `libhccl.so`（或恢复原软链）即可。

**Q：helper 版本不匹配报错？**
`libcannsim_helper.so` 须与 npusim wheel 版本精确一致（如均为 0.8.0），升级 npusim 后需同步替换 CANN 包内 helper。

## 注意事项

- 仿真速度远低于上板（双卡全核本例约 3 分钟），调大卡数/核数前先估算时长，用例内同步等待超时也需相应调大；
- `--chip N` 应与用例内 `HcclCommInitAll` 的 world size 一致，N 偏小会阻塞、偏大浪费仿真时间；
- `-n` 缺省时 report 自动回退 core 0；多核分析需在 record 与 report 两侧保持一致。
