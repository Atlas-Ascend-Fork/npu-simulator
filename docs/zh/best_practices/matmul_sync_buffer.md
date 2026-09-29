# 基于 Matmul 样例观察同步与 Buffer 依赖

## 概述

本文以 [CANN Samples](https://gitcode.com/cann/cann-samples) 中的 `Samples/0_Introduction/matmul` 为例，说明如何用较小 shape 快速仿真，并在NPUSim报告中观察 `SET_FLAG` / `WAIT_FLAG`、`GET_BUF` / `RLS_BUF` 这两类同步行为。同步控制相关背景可参考 CANN 文档中的[同步控制简介](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/latest/API/ascendcopapi/atlasascendc_api_07_0179.html)。

原始 `matmul` 样例已经包含 `AscendC::SetFlag` / `AscendC::WaitFlag`，适合展示 `SET_FLAG` / `WAIT_FLAG`。如果还需要观察 `GET_BUF` / `RLS_BUF` 及其流水连线，可以在 GM 到 L1、L1 到 L0 两个搬运阶段分别插入 `AscendC::Mutex::Lock` / `AscendC::Mutex::Unlock`，并让两组接口使用同一个 mutex ID。

## 接口与指令关系

| 目标指令 | AscendC API | CANN 文档 | 说明 |
| --- | --- | --- | --- |
| `SET_FLAG` | `AscendC::SetFlag` | [SetFlag/WaitFlag(ISASI)](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/latest/API/ascendcopapi/atlasascendc_api_07_0270.html) | 通知目标 pipe 事件已到达，需要与 wait 成对使用。 |
| `WAIT_FLAG` | `AscendC::WaitFlag` | [SetFlag/WaitFlag(ISASI)](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/latest/API/ascendcopapi/atlasascendc_api_07_0270.html) | 等待指定事件，避免单独 wait 造成长等待。 |
| `GET_BUF` | `AscendC::Mutex::Lock` | [Lock(ISASI)](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/latest/API/ascendcopapi/atlasascendc_api_07_00134.html) | 对应底层 `get_buf`，用于获取指定 buffer 资源。 |
| `RLS_BUF` | `AscendC::Mutex::Unlock` | [Unlock(ISASI)](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/latest/API/ascendcopapi/atlasascendc_api_07_00135.html) | 对应底层 `rls_buf`，用于释放指定 buffer 资源。 |

`AscendC::SetFlag` / `AscendC::WaitFlag` 用 `HardEvent` 模板参数描述源流水和目标流水。`AscendC::Mutex::Lock<pipe>(mutexId)` / `AscendC::Mutex::Unlock<pipe>(mutexId)` 用模板参数描述当前接口所在流水，用 `mutexId` 描述要获取和释放的 buffer 资源。

### 入参说明

`AscendC::SetFlag` / `AscendC::WaitFlag`：

- `event`：模板参数，类型为 `HardEvent`，名称格式为“源流水_目标流水”，例如 `MTE2_MTE1`。
- `eventID`：`int32_t` 类型的事件 ID。使用 `TPipe` 和 `TQue` 时可通过 `AllocEventID` 或 `FetchEventID` 获取；静态 Tensor 编程时由开发者管理，但不能使用 6 和 7。文档给出的范围为 0-3 或 0-7，具体取值范围与产品型号有关。

`AscendC::Mutex::Lock` / `AscendC::Mutex::Unlock`：

- `pipe`：模板参数，表示接口所在的流水类型，例如 `PIPE_MTE2` 或 `PIPE_MTE1`。
- `id`：用于流水同步管理的 `MutexID`。可由用户自定义，范围为 0-27；也可以通过 `AllocMutexID` / `ReleaseMutexID` 申请和释放。

## 准备样例

进入 `cann-samples` 仓库根目录，编译 `matmul`：

```bash
cd ~/cann-samples
cmake -S . -B build -DNPU_ARCH=dav-3510
cmake --build build --target matmul
cp ./Samples/0_Introduction/matmul/scripts/* ./build/Samples/0_Introduction/matmul/
cd ./build/Samples/0_Introduction/matmul
```

为了缩短仿真时间，建议先使用一个输出 tile 内的小 shape：

```bash
npusim record ./matmul -s Ascend950 -u '16 16 16' -g -n 0 -o out_put
```

`matmul` 样例 kernel 内部固定使用 `baseM = 256`、`baseN = 256`、`baseK = 256 / sizeof(T)`。当 `m`、`n` 小于 256 且 `k` 不超过 `baseK` 时，kernel 只需要处理一个输出 tile，适合快速验证同步指令是否被记录。

当前验证环境按照 `cannsim_helper/verify.md` 使用 `npusim` 命令。

## 观察原始 set/wait

原始 `Samples/0_Introduction/matmul/main.asc` 已经在多个流水阶段插入了 set/wait：

```cpp
AscendC::SetFlag<AscendC::HardEvent::MTE1_MTE2>(tool::ZERO_FLAG);
AscendC::SetFlag<AscendC::HardEvent::M_MTE1>(tool::ZERO_FLAG);
AscendC::SetFlag<AscendC::HardEvent::FIX_M>(tool::ZERO_FLAG);
```

本例中，GM 到 L1 的搬运位于 MTE2 流水，后续 L1 到 L0 的搬运位于 MTE1 流水。因此，GM 到 L1 搬运完成后，代码通过 `MTE2_MTE1` 事件通知后续 L1 到 L0 搬运：

```cpp
AscendC::Te::Copy(copyGM2L1, tensorAL1, tensorAGmTile);
AscendC::Te::Copy(copyGM2L1, tensorBL1, tensorBGmTile);

AscendC::SetFlag<AscendC::HardEvent::MTE2_MTE1>(tool::ZERO_FLAG);
AscendC::WaitFlag<AscendC::HardEvent::MTE2_MTE1>(tool::ZERO_FLAG);
```

L1 到 L0 搬运完成后，再用 `MTE1_M` 事件通知 MMAD：

```cpp
AscendC::Te::Copy(copyL12L0A, tensorAL0, tensorAL1Tile);
AscendC::Te::Copy(copyL12L0B, tensorBL0, tensorBL1Tile);

AscendC::SetFlag<AscendC::HardEvent::MTE1_M>(tool::ZERO_FLAG);
AscendC::WaitFlag<AscendC::HardEvent::MTE1_M>(tool::ZERO_FLAG);
```

仿真完成后，在生成的报告或 Perfetto tracing 文件中搜索 `SET_FLAG`、`WAIT_FLAG`。NPUSim会根据执行 pipe、消费 pipe、event id 和 core id 识别成对的 flag 依赖，因此同一个 event id 被重复使用时，也能按时间顺序串起依赖关系。

## 插入 get_buf/rls_buf

原始 `matmul` 不会直接生成 `GET_BUF` / `RLS_BUF`。如果要基于该样例同时展示指令和 buffer dependency flow，可以增加两组 AscendC API：

- 在 MTE2 流水使用 `Lock<PIPE_MTE2>` / `Unlock<PIPE_MTE2>` 包住 GM 到 L1 搬运。
- 在 MTE1 流水使用 `Lock<PIPE_MTE1>` / `Unlock<PIPE_MTE1>` 包住后续 L1 到 L0 搬运。

每个 pipe 内的 `Lock` / `Unlock` 自成一对，两组接口共享同一个 `MutexID`。原有的 `MTE2_MTE1` set/wait 保证两个搬运阶段的先后顺序。使用自定义 `MutexID` 时，不要同时使用 `TQue`、`TQueBind`、`TBufPool` 中的相关接口。

为了观察 GM 到 L1、L1 到 L0 两个阶段之间的 buffer 依赖，先在 `tool` namespace 中增加自定义 mutex ID `10`：

```cpp
constexpr static uint8_t GM2L1_MUTEX_ID = 10;
```

如果编译器提示 `AscendC::Mutex` 未定义，可在 include 区域按工程路径补充 AscendC 基础头文件：

```cpp
#include "kernel_operator.h"
```

先在 `MatmulKernel` 的 GM 到 L1 搬运段插入 MTE2 的 `Lock` / `Unlock`。建议放在 `WaitFlag<MTE1_MTE2>` 之后、`SetFlag<MTE2_MTE1>` 之前：

```cpp
AscendC::WaitFlag<AscendC::HardEvent::MTE1_MTE2>(tool::ZERO_FLAG);

AscendC::Mutex::Lock<PIPE_MTE2>(tool::GM2L1_MUTEX_ID);

auto copyGM2L1 = AscendC::Te::MakeCopy(AscendC::Te::CopyGM2L1{});
auto layoutAL1 = AscendC::Te::MakeLayoutAL1<T>{}(curM, curGmAKL1);
auto tensorAL1 = AscendC::Te::MakeTensor(
    AscendC::Te::MakeMemPtr<AscendC::Te::Location::L1, T>(l1BufferAOffset), layoutAL1);
auto tensorAGmTile =
    tensorAGmBlock.Slice(AscendC::Te::MakeCoord(0, iter0 * kL1), AscendC::Te::MakeShape(curM, curGmAKL1));
AscendC::Te::Copy(copyGM2L1, tensorAL1, tensorAGmTile);

auto layoutBL1 = AscendC::Te::MakeLayoutBL1<T>{}(curGmBKL1, curN);
auto tensorBL1 = AscendC::Te::MakeTensor(
    AscendC::Te::MakeMemPtr<AscendC::Te::Location::L1, T>(l1BufferBOffset), layoutBL1);
auto tensorBGmTile =
    tensorBGmBlock.Slice(AscendC::Te::MakeCoord(iter0 * kL1, 0), AscendC::Te::MakeShape(curGmBKL1, curN));
AscendC::Te::Copy(copyGM2L1, tensorBL1, tensorBGmTile);

AscendC::Mutex::Unlock<PIPE_MTE2>(tool::GM2L1_MUTEX_ID);

AscendC::SetFlag<AscendC::HardEvent::MTE2_MTE1>(tool::ZERO_FLAG);
AscendC::WaitFlag<AscendC::HardEvent::MTE2_MTE1>(tool::ZERO_FLAG);
```

然后在紧随其后的 L1 到 L0 搬运段插入 MTE1 的 `Lock` / `Unlock`：

```cpp
for (uint16_t iter1 = 0; iter1 < kL0IterNum; ++iter1) {
    AscendC::WaitFlag<AscendC::HardEvent::M_MTE1>(tool::ZERO_FLAG);

    AscendC::Mutex::Lock<PIPE_MTE1>(tool::GM2L1_MUTEX_ID);

    uint64_t curKL0 = (iter1 + 1 == kL0IterNum) ? tailKL0 : baseK;
    auto copyL12L0A = AscendC::Te::MakeCopy(AscendC::Te::CopyL12L0A{});
    auto copyL12L0B = AscendC::Te::MakeCopy(AscendC::Te::CopyL12L0B{});
    // 保留原始样例中 tensorAL0、tensorBL0、tensorAL1Tile、tensorBL1Tile 的构造代码。
    AscendC::Te::Copy(copyL12L0A, tensorAL0, tensorAL1Tile);
    AscendC::Te::Copy(copyL12L0B, tensorBL0, tensorBL1Tile);

    AscendC::Mutex::Unlock<PIPE_MTE1>(tool::GM2L1_MUTEX_ID);

    AscendC::SetFlag<AscendC::HardEvent::MTE1_M>(tool::ZERO_FLAG);
    AscendC::WaitFlag<AscendC::HardEvent::MTE1_M>(tool::ZERO_FLAG);
    // 保留原始样例中的 MMAD 代码。
}
```

重新编译并使用小 shape 仿真：

```bash
cd ~/cann-samples
cmake --build build --target matmul
cp ./Samples/0_Introduction/matmul/scripts/* ./build/Samples/0_Introduction/matmul/
cd ./build/Samples/0_Introduction/matmul
npusim record ./matmul -s Ascend950 -u '16 16 16' -g -n 0 -o out_put
```

报告中搜索 `GET_BUF`、`RLS_BUF`。在 tracing 里，`GET_BUF` 表示当前 pipe 尝试获取指定 buffer id，`RLS_BUF` 表示释放同一 buffer id。本例预期出现以下顺序：

```text
GET_BUF  PIPE:MTE2  bufId:0xa  cnt:0
RLS_BUF  PIPE:MTE2  bufId:0xa  cnt:0
GET_BUF  PIPE:MTE1  bufId:0xa  cnt:0x1
RLS_BUF  PIPE:MTE1  bufId:0xa  cnt:0x1
```

其中，流水图会从 MTE2 的 `RLS_BUF` 连到 MTE1 的 `GET_BUF`，表示 MTE1 获取同一 buffer 前依赖 MTE2 完成释放。

本例虽然有两组 `GET_BUF` / `RLS_BUF`，但只会生成一条 buffer dependency 连线。这是因为连线表示两次资源占用之间的等待关系，并不表示同一次占用中的 `GET_BUF` 到 `RLS_BUF`：

```text
MTE2 GET_BUF(cnt=0)       第一次获取，无前置依赖
MTE2 RLS_BUF(cnt=0)
        |
        +----------------> MTE1 GET_BUF(cnt=1)
                           MTE1 RLS_BUF(cnt=1)   后续无人获取
```

第一次 `GET_BUF` 之前没有其他流水占用该 mutex ID，因此没有入线；最后一次 `RLS_BUF` 之后没有新的 `GET_BUF`，因此没有出线。对于同一个 mutex ID，顺序获取 `N` 次时，最多形成 `N-1` 条 `RLS_BUF(cnt=n) -> GET_BUF(cnt=n+1)` 连线。

## 验证结果

按照 `cannsim_helper/verify.md` 的 helper 构建、部署、wheel 刷新和 record 流程验证，结果如下：

| 用例 | 结果 | 关键指令 |
| --- | --- | --- |
| AscendC API 跨流水示例，`16 16 16` | 编译、仿真、精度校验和 report 均通过 | `GET_BUF=2`、`RLS_BUF=2`，生成从 MTE2 `RLS_BUF` 到 MTE1 `GET_BUF` 的 buffer dependency flow |

以上验证的精度校验最大绝对误差为 0。跨流水示例的实际指令顺序为 MTE2 `GET_BUF(cnt=0)`、MTE2 `RLS_BUF(cnt=0)`、MTE1 `GET_BUF(cnt=1)`、MTE1 `RLS_BUF(cnt=1)`。

验证日志中可以看到 `cannsim_helper enabled` 和 `Found cannsim_helper DB input`，说明 helper 注入和 report 读取 DB 均已生效。当前环境的 `compare_instr_bin_db.py` 无法解析本次生成的 `instr.bin`，689 条记录均为 `decode_regex_miss`，因此不能用该脚本的 `missing_expected_exec_ids=0` 判断 DB 完整性；上表中的指令数量来自对 helper DB 中 `ExecutedInstructions` 表的直接查询。

## 报告查看重点

1、打开生成的 `trace_core*.json`，在 Perfetto 中搜索以下指令名：

```text
SET_FLAG
WAIT_FLAG
GET_BUF
RLS_BUF
```

2、查看原始 `SET_FLAG` / `WAIT_FLAG` 是否按 MTE2 到 MTE1、MTE1 到 M、M 到 FIXP 的顺序连接。若 `WAIT_FLAG` 的等待时间很长，说明消费 pipe 到达太早，生产 pipe 尚未完成。

3、查看 MTE2 和 MTE1 的 `GET_BUF` / `RLS_BUF` 是否使用相同 buffer id，并确认 MTE2 的 `RLS_BUF` 到 MTE1 的 `GET_BUF` 之间出现 `Pipe dependency (buffers)` 连线。若 `GET_BUF` 和 `RLS_BUF` 不成对，或中间路径存在提前 return、分支跳过释放，后续相同 buffer id 的 `GET_BUF` 可能出现不必要等待。

4、小 shape 适合验证指令是否出现以及 flag 依赖关系。是否生成 buffer dependency flow 取决于 `GET_BUF` / `RLS_BUF` 的实际执行顺序，不能只根据 shape 或指令数量判断。

## 常见问题

- 找不到 `AscendC::Mutex` 定义：确认当前 CANN 版本和目标架构支持 Mutex(ISASI) 接口，并按工程 include 约定补齐 `kernel_operator.h` 等 AscendC 基础 API 头文件。
- 编译 Tensor API 时出现 `max`、`min` 等模板重复定义：通常是 CANN 与 `third_party/ops-tensor` 版本不匹配，应切换到匹配的 CANN 包并重新配置构建目录。
- 报告中只有 `SET_FLAG` / `WAIT_FLAG`，没有 `GET_BUF` / `RLS_BUF`：确认已经插入 `AscendC::Mutex::Lock` / `AscendC::Mutex::Unlock`，并重新编译了 `matmul` target。
- `WAIT_FLAG` 等待异常长：检查 set 和 wait 的 pipe、event id 是否一致。
- `GET_BUF` 等待异常长：检查同一 `GM2L1_MUTEX_ID` 的 `Unlock` 是否一定会执行，避免只 lock 不 unlock。

## 注意事项

如果只在同一个 pipe（例如 `PIPE_MTE2`）中反复使用相同 mutex ID 执行 lock/unlock，虽然报告里可能出现多组 `GET_BUF` / `RLS_BUF` 指令，但流水图不一定生成 buffer dependency 连线。分析器需要观察到前一计数的 `RLS_BUF` 先于后一计数的 `GET_BUF`；同流水异步执行时，后一条 `GET_BUF` 可能先于前一条 `RLS_BUF` 出现，导致无法配对。需要稳定展示连线时，建议采用本文已验证的 MTE2 到 MTE1 跨流水交接写法，并以 trace 中的实际执行顺序为准。
