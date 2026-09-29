# npusim record -g vf 使用指南

## 1. VF 简介

VF（Vector Function）是 VectorCore 的核心计算单元，由一组连续的 vector 指令组成。Kernel 执行过程中，VF 之间串行执行，最慢的 VF 决定整体性能。

衡量 VF 性能的核心指标是 IPC（Instructions Per Cycle），其中 **exec 分量**反映向量执行算力的利用率。理论上限为 2（双发射）。

VF 按执行模式分为两类：

- **SIMD**（`type: "simd"`）— IPC 分量为 scalar / exec / ldst
- **SIMT**（`type: "simt"`）— IPC 分量为 branch / exec / ldst

`vf_report.json` 只输出从仿真 DB 直接统计的**客观数据**（IPC、timing、指令组成、指令时间线），不做主观评级或瓶颈分类——瓶颈判断交给使用方（人或 agent）。

---

## 2. 使用方法

### 2.1 命令

```bash
npusim record -s Ascend950 -g vf <user_app>
```

`-g vf` 在生成常规性能分析报告（summary.json、流水图等）的同时，额外导出结构化的 VF 性能分析 JSON。

### 2.2 输出位置

```
npusim_out/
└── npusim_<timestamp>_<kernel>/
    ├── report/
    │   └── results/            # 仿真报告目录（含 summary.json、trace流水图等）
    └── vf_report/
        └── kernel_launch_0_vf_report.json # VF 性能分析 JSON 报告
```


## 3. JSON 数据结构

顶层结构：

```json
{
  "version": "1.2",
  "kernel": { ... },      // Kernel 元信息
  "vfs": [ ... ],         // VF 定义列表（按函数分组，exec IPC 升序）
  "timeline": [ ... ]     // 全局 VF 指令时间线
}
```

### 3.1 kernel

| 字段 | 说明 |
|------|------|
| `name` | Kernel 名称 |
| `arch` | 架构版本 |
| `frequency_ghz` | 运行频率 |
| `active_cores` | 活跃核数 |
| `exec_time_ns` | 执行时间（纳秒） |
| `exec_time_ticks` | 执行时间（tick） |
| `total_instrs_executed` | 总执行指令数 |
| `scalar_ipc_overall` | 整体 Scalar IPC |

### 3.2 vfs[]

每个 VF 定义包含：

| 字段 | 说明 |
|------|------|
| `id` | VF 全局编号（按 exec IPC 升序后的序号） |
| `type` | `simd`（来源 VfIPC）/ `simt`（来源 VfSimtIPC） |
| `function_pc` | VF 函数体基地址（同一函数的多次调用共享此值） |
| `call_sites` | 所有调用该函数的 call-site PC 列表 |
| `executions` | 总调用次数 |
| `timing.avg/min/max_cycles` | 跨所有执行实例的 cycle 统计 |
| `timing.variance_ratio` | max/min 比值，反映执行波动 |
| `ipc.exec/ldst` | 执行 / 访存 IPC 分量 |
| `ipc.scalar` 或 `ipc.branch` | simd 有 scalar，simt 有 branch |
| `ipc.total` | 各分量之和（参考值） |
| `instruction_profile.total_count` | VF 内向量指令总数 |
| `instruction_profile.by_pipe[]` | 按 pipe 分类的指令统计（pipe/count/pct/ticks） |

> VF 按**函数入口 PC** 分组：编译器展开循环时，同一 VF 函数可能有多个 call-site PC，但它们共享相同的函数体代码。报告将这些调用合并为一个 VF 条目。

### 3.3 timeline[]

全局 VF 指令时间线，包含所有 VF 内部的 vector 指令，按 tick 排序：

| 字段 | 说明 |
|------|------|
| `tick_start` / `tick_end` | 指令执行的起止 tick |
| `pc` | 指令地址 |
| `name` | 指令名称（如 RV_VLD, RV_PLT） |
| `pipe` | 所属 pipe（RVECEX/RVECLD/RVECST/RVECLP/RVECSU） |
| `vf_call_pc` | 该指令所属 VF 调用的 call-site PC |
| `vf_function_pc` | 该指令所属 VF 函数的入口 PC |

---

## 4. 使用 jq 快速分析

```bash
VF_JSON=./kernel_launch_0_vf_report.json
```

### 4.1 查看 Kernel 概览

```bash
jq '.kernel' $VF_JSON
```

### 4.2 列出所有 VF 函数的 exec IPC（最差在前）

```bash
jq '.vfs[] | {id, type, function_pc, call_sites, executions, exec: .ipc.exec, ldst: .ipc.ldst}' $VF_JSON
```

输出：
```json
{"id":0,"type":"simd","function_pc":"0x304","call_sites":["0xb0","0xd8","0xfc"],"executions":3,"exec":0.942,"ldst":1.413}
{"id":1,"type":"simd","function_pc":"0x404","call_sites":["0x130","0x154","0x178"],"executions":3,"exec":1.428,"ldst":1.903}
{"id":2,"type":"simd","function_pc":"0x504","call_sites":["0x1c8","0x1ec","0x210"],"executions":3,"exec":1.544,"ldst":0.343}
```

### 4.3 找出 exec IPC 最差的 VF

```bash
jq '.vfs[0] | {id, type, function_pc, ipc, instruction_profile}' $VF_JSON
```

### 4.4 查看某个 VF 的指令 pipe 分布

```bash
jq '.vfs[] | select(.id == 0) | .instruction_profile.by_pipe[] | {pipe, pct, ticks}' $VF_JSON
```

### 4.5 只看某一执行模式的 VF（如 SIMT）

```bash
jq '.vfs[] | select(.type == "simt") | {id, function_pc, exec: .ipc.exec, branch: .ipc.branch}' $VF_JSON
```

### 4.6 查看执行波动最大的 VF

```bash
jq '[.vfs[] | {id, function_pc, variance_ratio: .timing.variance_ratio}] | sort_by(-.variance_ratio) | .[0]' $VF_JSON
```

### 4.7 查看某个 VF 函数的 timeline 指令数

```bash
jq '[.timeline[] | select(.vf_function_pc == "0x304")] | length' $VF_JSON
```

### 4.8 查看某次特定调用的 timeline

```bash
jq '[.timeline[] | select(.vf_call_pc == "0xb0")] | .[0:5]' $VF_JSON
```

### 4.9 统计 timeline 中各 pipe 的指令数

```bash
jq '[.timeline[] | select(.vf_function_pc == "0x304")] | group_by(.pipe) | map({pipe: .[0].pipe, count: length})' $VF_JSON
```

---

## 5. 典型分析流程

1. `jq '.kernel.exec_time_ns'` — 整体耗时是否符合预期
2. `jq '.vfs[0]'` — 定位 exec IPC 最差的 VF 函数
3. 看 `ipc.exec` / `ipc.ldst` — 判断算力利用率与访存占比
4. 看 `instruction_profile.by_pipe` — 了解指令组成
5. 用 `timeline` 按 `vf_function_pc` 过滤 — 查看具体指令执行时序
6. 区分 `type`（simd/simt）— 按执行模式分别分析

> 多核均衡、Pipe 利用率、关键路径、带宽等全局指标不在 `vf_report.json` 范围内，请查看 `summary.json` 与 `aicore_utilization.json` 报告。
