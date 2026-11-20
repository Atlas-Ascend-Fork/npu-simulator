# Changelog

本文件记录 NPUSim（npusim / cannsim）的各版本变化、兼容性与弃用事项。

## Unreleased

### 新增

- `npusim record` 新增仿真超时自动停止：`--timeout` 指定最大仿真时长（纯数字为秒，或带 `s`（秒）后缀，如 `30s`；`0` 不限制，默认关闭），超时后自动终止整个仿真进程组（SIGTERM → 5s 宽限 → SIGKILL）并归档已产出的部分产物；亦可通过环境变量 `NPUSIM_TIMEOUT` 设定，CLI 优先。超时按普通失败退出（退出码 `1`），终端 Summary 与 `npusim.log` 会以 TIMEOUT 标注明确提示超时原因。经典 record 模式与 ktest 模式（`record -c`）均支持。
