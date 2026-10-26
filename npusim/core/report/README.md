# Report 模块

性能分析报告生成模块，负责从模拟执行产物中生成结构化报告。

## 架构概览

```
report/
├── __init__.py               # 模块入口，导出 generate_report / report
├── report.py                 # CLI 入口 & 主编排逻辑
├── report_path.py            # 文件系统与路径工具（查找、解析、重组）
├── report_strategy_base.py   # 抽象策略基类 + 后端可用性检测
└── report_strategy_impl.py   # 具体策略实现 + 执行链
```

## 核心设计：策略模式

报告生成采用 **策略模式**，通过 `FallbackChain` 执行当前可用的报告后端：

```
BiProfModuleStrategy
（高保真 HTML 报告）
```

| 后端 | 依赖 | 产出 | 保真度 |
|------|------|------|--------|
| BiProfRunner 模块 | 纯 Python（标准库） | HTML/JSON 报告 + tracing JSON | 高 |

> 旧版设计曾包含额外报告后端；当前实现不再查找或调用它们。

### 文件职责分工

| 文件 | 职责 | 关键符号 |
|------|------|----------|
| `report_strategy_base.py` | 抽象基类 `ReportStrategy`、后端检测（BiProfRunner 延迟导入）、SoC→Arch 映射 | `ReportStrategy`, `get_biprof_runner_module()`, `SOC_TO_ARCH` |
| `report_strategy_impl.py` | 具体策略类 `BiProfModuleStrategy`、执行链 `FallbackChain`、向后兼容别名 | `REPORT_CHAIN`, `_REPORT_CHAIN` |
| `report_path.py` | 路径解析校验、instr.bin / log_ca 查找、BiProfRunner 参数构建、core_id 解析、报告目录重组、tracing report 收集与重命名 | `_resolve_paths()`, `_find_instr_bin()`, `_build_biprof_args()`, `_reorganize_reports_by_core()`, `_collect_trace_reports()` |
| `report.py` | 主编排 `generate_report()`：路径解析 → 日志源查找 → core_id 解析 → 参数构建 → 后端执行 → 产物重组 | `generate_report()`, `report()` |

### 为什么把 base 和 impl 分开？

`report_strategy_base.py` 只包含抽象定义和依赖检测，**不导入任何具体策略类**。这样：
- 只需要判断后端可用性的模块不会意外触发 BiProfRunner 的重导入链。
- 具体策略实现可以独立修改，不影响基类和检测逻辑的稳定性。

## 主流程（`generate_report`）

```
① 解析并校验路径（_resolve_paths）
② 查找日志源：instr.bin 优先 → log_ca/ fallback
③ 解析 core_id
④ 构建 biprof_args → _REPORT_CHAIN.execute()
⑤ 后处理产物目录结构：
   - 重组 kernel_*_reports/ 下的 per-core 文件
⑥ VF report JSON 由 BiProf backend 在 DB 连接生命周期内导出（export_vf=True 时透传 `--vf-report-json`）
```

## 目录结构约定

### 输入（export 目录）

```
export/
├── instr.bin          # 优先日志源（二进制格式）
├── log_ca/            # fallback 日志源（文本格式）
└── record/            # 新目录结构兼容
    ├── instr.bin
    └── log_ca/
```

### 输出（output / report 目录）

BiProfRunner 重组前：
```
kernel_0_reports/
    core_0_icache_report.html
    core_0_tracing_report_0.json
    aicore_utilization.json
```

重组后（`_reorganize_reports_by_core` 执行）：
```
kernel_0_reports/
    aicore_utilization.json              ← 非 per-core 文件留在根目录
    core_0/
        core_0_icache_report.html
        trace_core0.json                 ← tracing_report 重命名
    core_1/
        ...
```

## VF Report JSON 结构

`cannsim/prof/src/backend/vf_export.py` 复用 BiProf backend 当前 DB 连接提取数据，生成 `{db_stem}_vf_report.json`：

```json
{
  "version": "1.2",
  "kernel": { "name", "arch", "frequency_ghz", "exec_time_ns", ... },
  "vfs": [
    {
      "id": 0, "type": "simd|simt",
      "function_pc": "0x...", "call_sites": ["0x..."],
      "executions": N,
      "timing": { "avg_cycles", "min_cycles", "max_cycles", "variance_ratio" },
      "ipc": { "scalar|branch", "exec", "ldst", "total" },
      "instr_profile": { "total_count", "by_pipe": [...] }
    }
  ],
  "timeline": [ { "tick_start", "tick_end", "pc", "name", "pipe", ... } ]
}
```

VF 按 exec IPC 从低到高排序（最需要优化的在前），同时支持 SIMD 和 SIMT 两种执行模式。
