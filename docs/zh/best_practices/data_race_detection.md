# 基于数据竞争检测报告定位 RAW/WAR/WAW 问题

## 概述

NPUSim 在执行 `record` / `report` 时会自动运行数据竞争检测，基于仿真采集到的指令与访存记录，检查同一地址上是否存在缺少同步保护的读后写（RAW）、写后读（WAR）、写后写（WAW）冲突。检测结果写入独立的文本报告，便于在现象上板的偶发错误之前，先从仿真侧定位跨流水、同流水、GM（HBM）以及 UB 跨子核的竞争问题。

检测覆盖以下四个域：

- **UB / L1 等片上 Buffer 的跨流水竞争**：VEC、MTE1、MTE2、MTE3、SCALAR、CUBE、FIXP 之间缺少 `SET_FLAG` / `WAIT_FLAG` 同步链。
- **UB / L1 等片上 Buffer 的同流水竞争**：同一流水内相邻指令（指令窗口内）对同一地址的竞争。
- **GM（HBM）竞争**：同核流水间、跨核之间的 GM 读写竞争，跨核竞争由跨核向量时钟判定 happens-before。
- **UB 跨子核竞争**：Ascend950 上一个 block 内 cubecore 与 veccore 共享 UB 时的竞争，由 `SET_INTRA_BLOCK` / `WAIT_INTRA_BLOCK` 判定同步。

> 检测结论用于辅助定位问题。报告中出现竞争不代表算子一定错误，但应结合代码确认是否存在缺失的同步或非预期的指令重排。

## 检测输入

数据竞争检测完全基于 cannsim_helper 生成的 SQLite record DB（`npusim record` 产物目录下的 `record/*.db`），不依赖文本日志。主要读取以下表：

| 表 | 用途 |
| --- | --- |
| `ExecutedInstructions` | 指令 ID、核 ID、核类型、指令名、PC，用于把访存事件归属到具体指令与流水。 |
| `MemoryRWAccesses`（`MemoryType > 0`） | 片上 Buffer 的读写地址、长度、Step、访问时刻，构成 UB/L1 竞争检测的事件集。 |
| `MemoryRWAccesses`（`MemoryType = 0`） | GM/HBM 的读写事务，构成 GM 竞争检测的事件集。 |
| `KernelInfo` | 读取 `start_pc`，把 `SourceInstrAddr` 还原为绝对 PC。 |

检测需要 **contract-v7** 的 record DB（`MemoryRWAccesses` 含 `Size` 列）。DB 由与当前 wheel 同版本的 cannsim_helper 生成；版本不一致时 report 会直接报错退出。若输入目录没有 record DB（例如仅 `instr.bin`），检测会跳过并写出空结果，不会让 `report` 失败。

## 生成报告

### 方式一：仿真时自动生成

```bash
npusim record ./your_app -s Ascend950 -g
```

`-g` 会在仿真结束后自动执行 `report`，其中包含数据竞争检测。

### 方式二：对已有仿真结果单独生成

```bash
npusim report -e /path/to/npusim_{timestamp}_{user_app} -o /path/to/report
```

### 输出文件

报告输出目录下会生成：

```text
report/
├── data_race_detection_report.txt        # 数据竞争检测报告
├── sync_instruction_detection_report.txt # 同步指令检测报告（同时生成）
├── pc_source_map.json                    # PC→源码缓存（启用源码映射时）
└── results/                              # 指令流水图、利用率等性能报告
```

`data_race_detection_report.txt` 位于 report 输出根目录，与 `results/` 同级。

### 补充源码行号

默认情况下报告只给出设备 PC。若需要把 PC 映射到源码文件与行号，可在编译时带 `-g` 生成带调试信息的设备目标文件，并在 report 时显式指定：

```bash
npusim report -e /path/to/npusim_{timestamp}_{user_app} \
    --object-file /path/to/your_kernel.asc.o
```

未显式指定时，report 会回退到 `record` 阶段记录的 `.user-app` 可执行文件。源码映射依赖 CANN 工具包中的 `msobjdump` 与 `llvm-symbolizer`，缺少时对应行会留空，不影响竞争检测本身。

## 检测规则

默认规则集按域分为五组，共 15 条规则：

| 规则 | 说明 |
| --- | --- |
| `CROSS_PIPE_RAW_NO_SYNC` | 跨流水 W→R，地址重叠，两流水间无 `SET_FLAG`/`WAIT_FLAG` 同步链。 |
| `CROSS_PIPE_WAW_NO_SYNC` | 跨流水 W→W，地址重叠，无同步链。 |
| `CROSS_PIPE_WAR_NO_SYNC` | 跨流水 R→W，地址重叠，无同步链。 |
| `SAME_PIPE_RAW` | 同流水 W→R，地址重叠，且两条指令的 `instr_id` 差值不超过 16。 |
| `SAME_PIPE_WAR` | 同流水 R→W，地址重叠，且两条指令的 `instr_id` 差值不超过 16。 |
| `SAME_PIPE_WAW` | 同流水 W→W，地址重叠，且两条指令的 `instr_id` 差值不超过 16。 |
| `GM_WAW_OVERLAP` | 同核 GM W→W 地址重叠，且中间没有 READ 消费前一次写。 |
| `GM_RAW_RACE` | 同核 GM W→R，地址重叠，指令不同。 |
| `GM_WAR_RACE` | 同核 GM R→W，地址重叠，指令不同。 |
| `GM_CROSS_CORE_WAW` | 跨核 GM W×W，地址重叠，跨核向量时钟判定无同步。 |
| `GM_CROSS_CORE_RAW` | 跨核 GM W×R，地址重叠，无跨核同步。 |
| `GM_CROSS_CORE_WAR` | 跨核 GM R×W，地址重叠，无跨核同步。 |
| `UB_CROSS_CORE_WAW` | 同一 block 内 cubecore 与 veccore 对同一 UB 地址 W×W，无 `SET_INTRA_BLOCK` 同步。 |
| `UB_CROSS_CORE_RAW` | 同一 block 内 cubecore 与 veccore 对同一 UB 地址 W×R，无同步。 |
| `UB_CROSS_CORE_WAR` | 同一 block 内 cubecore 与 veccore 对同一 UB 地址 R×W，无同步。 |

判定要点：

1. **流水的确定**：优先使用 `ExecutedInstructions` 中 `ExtendParams.queue_type` 记录的流水；缺失时按指令名前缀回退推断（如 `RV_`、`V[A-Z]` 视为 VEC）。
2. **跨流水同步链**：`SET_FLAG(src_pipe) → WAIT_FLAG(dst_pipe)` 只要存在直接或传递（最多 4 跳）的链路，即认为两个流水满足 happens-before，不再报告。
3. **同流水指令窗口**：同流水指令按 FIFO 顺序执行，只有 `|Δinstr_id| <= 16`（issue queue 可并发的范围）时才认为可能竞争；超出窗口视为严格有序。
4. **TQue 语义**：存在 `GET_BUF` / `RLS_BUF` 的 buffer 由计数握手保证生产消费顺序，同流水 RAW/WAR 视为正常，仅保留 `SAME_PIPE_WAW`。
5. **跨核 happens-before**：`BAR`、`SET_CROSS_CORE` / `WAIT_FLAG_DEV` 参与跨核向量时钟推进；只有时钟判定为并发时才报告。跨核竞争报告中 severity 为 `warning`，其余为 `error`。
6. **GM 写消费**：同核 GM 的 WAW 若中间存在对同一地址的 READ，则视为前一次写已被消费，不再报告 WAW。

## 报告解读

无竞争时报告内容为：

```text
[data_race_detection] No violations found.
```

存在竞争时按域分段输出，下面是跨流水 WAW 的示例：

```text
[data_race_detection] Found 1 data-race violation(s):

Found 1 cross/same-pipe data-race violation(s):

=== Kernel: core0.vector0 (1 violations) ===
  [CROSS_PIPE_WAW_NO_SYNC | cross-pipe WAW (no sync chain)] core=core0.vector0 pc=0x208 conflict instrs: 129→130 op=WRITE pipe=MTE3 buf_id=-1
    WRITE instr_id=130 pipe=MTE3 range=[0x8000,0x8040) cycle=120 overlaps WRITE instr_id=129 pipe=VEC range=[0x8000,0x8040) cycle=100, no SetFlag/WaitFlag chain (VEC->MTE3) sync
    raw: MemoryRWAccesses MemoryType=1 CoreId=0 AccessMode=W AccessTime=120 Addr=0x8000 Size=64 InstrName=STORE_2DV2 Pc=0x208
    prev raw: MemoryRWAccesses MemoryType=1 CoreId=0 AccessMode=W AccessTime=100 Addr=0x8000 Size=64 InstrName=STORE_2DV2 Pc=0x204
```

关键字段说明：

| 字段 | 含义 |
| --- | --- |
| `rule` | 规则名与中文/英文描述，例如 `CROSS_PIPE_WAW_NO_SYNC`。 |
| `core` / `cores` | 发生竞争的核；跨核规则会同时列出两侧核号或子核名。 |
| `conflict instrs` | 冲突指令对，格式为 `prev_instr_id→cur_instr_id`。 |
| `pc` | 当前侧（较晚一侧）设备 PC。 |
| `op` / `pipe` | 当前侧访问类型与流水。 |
| `range` | 访问地址区间 `[lo, hi)`，多段命中时按 `min~max` 合并并标注段数。 |
| `cycle` | 两侧事件在仿真中的发生时刻。 |
| `raw` / `prev raw` | 触发该竞争的原始 DB 记录，可直接回查记录来源。 |
| `src` | 启用源码映射后给出的源码文件与行号。 |

报告会把同一规则、同一对冲突指令的多次命中聚合为一条 `xN` 记录，并把地址区间合并为最小到最大的范围，避免同一问题重复刷屏。

## 典型案例

### 缺失跨流水同步导致 WAW

下面的写法让 VEC 与 MTE3 写同一段 UB，但没有在两条指令之间插入同步：

```cpp
// VEC 流水写 UB
AscendC::Te::Copy(copyL0C2UB, tensorUB, tensorL0C);

// MTE3 流水紧接着写同一段 UB，缺少 V_MTE3 同步
AscendC::Te::Copy(copyUB2GM, tensorGM, tensorUB);
```

仿真后报告会给出 `CROSS_PIPE_WAW_NO_SYNC`，并指出冲突指令来自 VEC 与 MTE3。修复方式是在生产侧设置事件、消费侧等待该事件：

```cpp
AscendC::SetFlag<AscendC::HardEvent::V_MTE3>(eventId);
AscendC::WaitFlag<AscendC::HardEvent::V_MTE3>(eventId);
```

重新仿真后，`SET_FLAG`/`WAIT_FLAG` 构成的同步链会让该竞争从报告中消失。

### 同步正确时报告为空

以 [基于 Matmul 样例观察同步与 Buffer 依赖](./matmul_sync_buffer.md) 中的 `matmul` 样例为例，GM→L1、L1→L0 与 MMAD 之间已经通过 `MTE2_MTE1`、`MTE1_M` 等事件同步。对这类正确插入同步的访存，检测会建立 happens-before 关系，报告通常为 `No violations found.`。如果仍出现竞争，应优先检查 set/wait 的 pipe 方向、event id 是否配对，以及是否存在提前 return 跳过了释放路径。

## 常见问题

- **报告不存在或为空**：确认 `npusim record` 使用与当前 wheel 同版本的 cannsim_helper，并检查 export 目录下是否存在 `record/*.db`。
- **提示版本不匹配**：record DB 的 `helper_version` 必须与当前 cannsim wheel 完全一致，升级其中较旧的一方后重新采集。
- **只有 GM 竞争、没有 UB 竞争**：确认 DB 中 `MemoryRWAccesses` 含 `Size` 列（contract-v7）。旧版 DB 会跳过片上 Buffer 检测。
- **报告有竞争但代码看起来有同步**：检查 `SET_FLAG`/`WAIT_FLAG` 的源/目标 pipe 是否与事件实际流水一致；同步链方向错误时不会被识别。
- **源码行号为空**：检查是否在编译时带 `-g`，以及 `--object-file` 指向的设备目标文件是否正确，环境中是否具备 `msobjdump` 与 `llvm-symbolizer`。
- **同一问题多次出现**：报告已按冲突指令对聚合；若地址范围很大，说明该指令在多处访问了同一片区域，需要结合 `raw` 记录确认。

## 注意事项

1. 数据竞争检测是静态时序推断，结论依赖仿真记录到的指令、流水与同步信息；建议结合指令流水图和源码人工复核后再修改代码。
2. 跨核 GM 竞争使用 `warning` 级别输出，因为跨核同步是否完整无法只凭单次局部记录完全确认。
3. 同流水检测只覆盖 `|Δinstr_id| <= 16` 的指令窗口；窗口外的访存按 FIFO 严格有序处理，不会报告。
4. 检测默认不使用 scanline + VectorClock 候选集，以避免与 `CROSS_PIPE_*` 重复报告；如需自定义规则流水线，可单独接入 `ScanRaceRule`。
5. 大用例的检测会遍历全部访存事件，耗时随事件数增长；调试阶段可用 `-n` 聚焦关键核，但跨核竞争需要覆盖相关核才能发现。
