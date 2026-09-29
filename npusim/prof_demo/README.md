# prof_demo

`npusim` 的 JSON 报告子模块。算法抽取自已删除的 `cannsim/prof`，只使用标准库，不需要 CANN toolkit、collector 或 Plotly。`npusim report` / `npusim record -g` 通过 `npusim.core.report` 调用这里。

## 运行

兼容 Python 3.7+，仅使用标准库。`sqlite3` 链接的 SQLite 需为 3.8.0+（支持 `PRAGMA query_only`），不需要 JSON1 扩展。在仓库根目录执行：

```bash
python3 -m npusim.prof_demo \
  --input-db /path/to/0000_gather.db \
  --output /path/to/new_reports
```

`--input-db` 也可指向包含 `.db` 文件的目录，按文件名排序逐个处理。当前支持 `v100`、两表事实合同 `contract_version=6/7`：`KernelInfo` 和 `ExecutedInstructions`，其中 `ExtendParams` 提供解码语义。没有原始日志解析或旧数据库格式回退。

产物只有：

```text
new_reports/
  kernel_0000_gather_reports/
    core_0_tracing_report_0.json[.gz]
    aicore_utilization.json
    summary.json
    0000_gather_vf_report.json   # 仅 --vf-report-json，且 DB 含 VF/VFI/VF_SIMT 调用时
```

VF report 文件名取各自输入 DB 的文件名 stem（与原模块一致），不是 kernel 名。

输出目录必须不存在；不覆盖、不清空已有目录。同批 DB 若产生相同 kernel 目录名，明确报错。生成阶段在临时目录完成后发布，分析失败不会发布缺少报告的输出。日志仅写终端，不生成日志文件。输入 DB 以 SQLite `mode=ro` 附加，派生分析数据仅存在内存中，不留下 `analysis.db`。

随 `npusim` 一起打包，不再是仓库根下的独立包。

## 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--core-id` | `all` | `all`、`0`、`0-2,5`；与已有 core 取交集，无匹配时报错。原 `cannsim report` 默认只处理 core 0，见下文 |
| `--sliding-window` | `64` | scalar IPC 窗口指令数，必须为正整数 |
| `--sliding-window-vec` | `8` | VF IPC 窗口指令数，必须为正整数 |
| `--json-max-size` | `524288000` | trace 分片目标大小；`-1` 关闭分片 |
| `--no-ticks-to-ns` | 关闭 | 兼容原行为，将 1 tick 当作 1 ns 展示 |
| `--vf-report-json` | 关闭 | 额外导出 `<db_stem>_vf_report.json`，对应原 `npusim.prof report --vf-report-json`；无 SIMD/SIMT VF IPC 结果时不生成文件。关闭时其余产物与未加此功能前逐字节一致 |

`--core-id` 默认 `all`，与原上层入口不同：`cannsim report` 调用 `npusim.prof` 时传 `core_id or "0"`（`cannsim/core/report/report_path.py`），即未指定时只处理 core 0。需要与 `cannsim report` 默认结果对齐时显式传 `--core-id 0`。

默认计算 scalar 和 vector IPC，对应原上层 report 调用启用 `--scalar-ipc --vec-ipc` 的行为。原模块只有同时传 `--vec-ipc` 才会计算 VF IPC 并导出 VF JSON；本模块总是计算，所以 `--vf-report-json` 单独生效。无 HTML 开关、smart-pipes、禁用 analyzer 或采集参数。

按 DB/core 顺序执行，无进程池和 `-j`。内存使用与 DB 和单 core 的事件数量相关；分片不是流式生成，不承诺恒定内存。

分片沿用原 payload 大小估算，重复 metadata 以便各片独立打开；metadata/单条超大事件可能使文件超过目标。序列化文本长度超过 5 MiB 时自动 gzip。时间戳按 Chrome Trace 约定使用微秒，`displayTimeUnit` 保持 `ns`。

## 保留的语义

- 指令时间线、公开指令参数、同步 buffer/flag flow、scalar/VF IPC、unit utilization counter。
- `aicore_utilization.json` 是每个 core/subcore 的活跃**跨度**及其占全局跨度的比例，不是区间并集忙碌率。
- summary 保留 kernel、流水线利用率、诊断、scalar/vector 指令统计、cache 字段、13 类带宽候选、流水线重叠率和 SIMD VF 指标。无事务的带宽项仍省略。
- 保留旧 core 选择口径：trace、pipe utilization、scalar IPC 和 unit counters 按选择过滤；occupancy、VF IPC 及其他 kernel 级汇总仍可覆盖全部 core。不是全局过滤器。
- 不输出原始 encoding/operand，也不读取 toolkit 的 `exclude_instr.toml`。对照基线应使用未加载外部 exclusion 配置的普通 trace。
- VF report JSON 结构、排序、舍入和原有口径全部沿用 `cannsim/prof/src/backend/vf_export.py`（version `1.2`），包括原有怪异点：`timeline` 只覆盖 `VF` 调用（不含 `VFI`/`VF_SIMT`），function PC 查找不按 core 过滤，VF 列表覆盖全部 core。输出值均有限，`allow_nan=False` 不会触发；原版以 `ensure_ascii=False` 写出，本模块与其他报告一致使用默认 `ensure_ascii`，字节可能不同但 JSON 解析结果相同（输出字段均为 ASCII 时字节也相同）。
- `kernel.scalar_ipc_overall` 由最小恢复的原 `scalar_ipc_analyzer` 计算，不再落 `AnalysisSummary` 表：按 `--core-id` 选择的 core，对每个 subcore 取非 RVEC 指令数 / `MAX-MIN(ExecInstrTickStart)`（两表合同下 `DispatchTime` 恒为空，原 `COALESCE` 等价于 `ExecInstrTickStart`），跳过跨度为 0 的 subcore，subcore 平均后再对 core 平均，`round(..., 2)`，输出为字符串（原值写入 VARCHAR 列后以 TEXT 读出，如 `"0.07"`）。边界：若某个选中 core 的所有 subcore 跨度都为 0，原 analyzer 除零失败被框架吞掉、不写指标，导出为 `null`；本模块显式返回 `null`，结果相同，正常路径不变。

## 删除的内容与已知限制

不包含 SVG/HTML/warp utilization 报告、Plotly/架构图模板、collector 版本探测、smart-pipe 队列拆分、多层 launcher/manager 框架、性能日志、无消费者的 units-usage/roofline 分析和 `AnalysisSummary` 表（scalar 总体 IPC 只作为 VF JSON 字段按需计算）。

当前两表合同没有 cache/dispatch/region/debug/warp 等事件的生产者，因此删除这些占位空表、JOIN 和空轨道。`cache` 仍返回原版的 `0/null`；缺失 clock/name histogram 导致的 SIMD VF 零值字段也保持不变，**不能解读为真实测得的零**。

SIMT 叶指令 `SIMT_*` 在 RVEC 类型中的展示缺失是原逻辑的已知问题：本模块继续跳过它们，仅保留 `VF_SIMT` 调用等有效事件，不虚构 warp 分组。本次没有修复这项行为。

对比允许删除 metadata-only 空轨道和重新编号 PID，其余有效事件及指标应一致。不顺便修改原 summary 的指标命名和计算口径。对原来会除零的边界做明确处理：零全局跨度拒绝生成；局部零时长 scalar IPC 分母至少为一个周期；无法计算的 imbalance ratio 为 `null`；无 scalar 指令的比例为 `0`。

## 测试

```bash
python3 -m unittest tests.test_core.test_prof_demo -v
# Python 3.7 环境使用同一套测试：
python3.7 -m unittest tests.test_core.test_prof_demo -v
```

覆盖多核、SIMD、DMA、CUBE/FIXP 参数、同步流、源 DB 只读、输出白名单、压缩分片、窗口/零时长边界、无效 JSON、重复输出、多 DB、Python 3.7 语法、无 SQLite JSON1 扩展和脱离 `cannsim`/site-packages 的运行。

可选真实样例对照。`cannsim/prof` 已删除，基线只能用删除前保存的报告目录：

```bash
PROF_DEMO_SAMPLE_DB=/path/to/0000_gather.db \
PROF_DEMO_BASELINE_DIR=/path/to/legacy/results/kernel_0000_gather_reports \
python3 -m unittest tests.test_core.test_prof_demo.SampleParityTest -v
```

也可用 `cannsim report` 产物目录（如 `report/results/0000_gather_reports`）作基线：其 trace 被重命名为 `core_<N>/trace_core<N>.json[.gz]`，测试会递归识别两种命名。但 `cannsim report` 默认只处理 core 0，多核 DB 需要其以全部 core 生成，否则与本工具默认 `all` 的 trace/summary 不一致。

对照 summary/occupancy 的解析结果（基线含 `*_vf_report.json` 时，还以 `--vf-report-json` 重新生成并对比解析结果和文件名），以及忽略 PID 编号差异后的 trace 有效事件多重集；基线 trace 事件为空时直接报错；同时验证源 DB SHA-256 不变。
