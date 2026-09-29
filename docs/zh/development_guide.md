# NPUSim 代码规范与本地验证指南

本文档说明本仓的代码规范、提交前检查与本地验证命令，供贡献者准备 PR 时参考。

## 一、代码规范

本仓是纯 Python 工具仓。代码规范由 `ruff` 统一约束，配置见根目录 `pyproject.toml`。

### 1. 风格约束

| 项 | 取值 | 说明 |
| --- | --- | --- |
| 行长度 | 120 | `line-length = 120` |
| 目标版本 | Python 3.7 | `target-version = "py37"` |
| Lint 规则 | F + E9 | F（pyflakes）、E9（语法错误） |

### 2. Python 3.7 兼容（重要）

`setup.py` 声明 `python_requires=">=3.7"`，代码必须兼容 Python 3.7。以下写法**禁止**：

- 海象运算符 `:=`（3.8）
- `match` 语句（3.10）
- 内置泛型注解 `list[int]`、`dict[str, int]` 直接求值（3.9）——除非模块顶部有 `from __future__ import annotations`
- `str.removesuffix` / `str.removeprefix`（3.9）
- PEP 604 联合类型 `X | Y` 标注直接求值（3.10）；即使延迟注解可通过静态检查，仍须考虑运行时解析注解的兼容性

CI 中由 `.gitcode/scripts/py37_compat.sh` 专项检查，本地可执行：

```bash
bash .gitcode/scripts/py37_compat.sh 3.7
```

### 3. 拼写（codespell）

`codespell` 使用 CANN/ascend 等领域词白名单；当前配置排除了 Python、Markdown 等文件，不能将此检查通过当作全部代码和文档拼写正确的证明。本地复现配置：

```bash
pip install codespell
codespell -L CANN,cann,NNAL,nnal,ASCEND,ascend,EnQue,CopyIn,ArchType,AND,ND,tbe,copyin,alog,wIh,dout,outWeight \
  --skip "*.py,*.cpp,*.hpp,*.c,*.h,*.md"
```

## 二、提交前检查（pre-commit）

本仓通过 `pre-commit` 在提交时自动执行以下钩子：

| 钩子 | 作用 |
| --- | --- |
| trailing-whitespace | 去行尾空格 |
| end-of-file-fixer | 文件末尾补换行 |
| check-yaml | YAML 语法检查 |
| check-added-large-files | 禁止提交大文件 |
| check-merge-conflict | 禁止合入冲突标记 |
| detect-private-key | 检测私钥 |
| check-json | JSON 语法检查 |
| clang-format | C/C++/asc 格式化（本仓无此类源码，预留） |
| ruff-check | Python lint（`--fix`） |
| ruff-format | Python 格式化（`--check --diff`） |
| codespell | 拼写检查 |
| oat-check | OAT 开源合规检查 |

安装并启用：

```bash
pip install pre-commit
pre-commit install
```

之后每次 `git commit` 会自动执行上述检查。如已提交后发现格式问题，可手动执行：

```bash
pre-commit run --all-files
```

## 三、本地验证命令

PR 提交前建议在本地完整跑一遍以下检查：

### 1. 安装依赖

开发工具建议使用 Python 3.11；在独立虚拟环境安装，避免脚本自动安装工具时影响系统 Python。运行代码仍按 Python 3.7 做兼容检查，工具环境不等同于运行支持范围。

```bash
python3.11 -m venv .venv
source .venv/bin/activate  # Linux / macOS；Windows 使用 .venv\Scripts\activate
python -m pip install -e .
python -m pip install -r tests/requirements.txt
python -m pip install ruff==0.14.14 "pre-commit>=4.0.0" vermin "oat-py>=1.0.1"
```

NPUSim 核心功能基于 Python 标准库实现，零外部运行时依赖。测试依赖在 `tests/requirements.txt`；ruff 版本与 `.pre-commit-config.yaml` 一致，后续升级需同步说明。

先运行不依赖 NPU、CANN 或内部账号的基础测试：

```bash
python -m pytest tests/test_core/test_prof_demo.py -v
```

这些用例使用合成数据和临时 SQLite DB。真实采集/仿真验证需额外准备 CANN 与匹配版本的 cannsim_helper，并遵循[快速入门](quick_start/quick_start.md)；基础 UT 通过不代表真实硬件链路已验证。

### 2. Lint（ruff）

```bash
ruff check npusim/ tests/
```

### 3. 格式化检查（ruff format）

```bash
ruff format --check --diff npusim/ tests/
```

### 4. 单元测试（pytest）

```bash
python -m pytest tests/ -v --tb=short --cov=npusim --cov-report=term
```

### 5. Python 3.7 兼容检查

```bash
bash .gitcode/scripts/py37_compat.sh 3.7
```

### 6. 开源合规检查（OAT）

```bash
bash .gitcode/scripts/oat_check.sh
```

OAT 和 py37 脚本可能自动安装缺失工具，需联网；OAT 会生成扫描报告。pre-commit 首次运行也需联网下载公开 hooks。基础检查不要求内部账号，但需具备访问公开依赖源的网络条件。

### 7. 验证记录

在 PR 中记录 Python/工具版本、实际命令、通过/失败/跳过数量及未验证原因。纯模板/文档修改至少核对 front matter、链接、引用的代码入口和命令；合入默认分支后还需维护者在 GitCode 创建页验证模板实际预填，不以静态检查代替平台验收。

## 四、失败排查

- **ruff 报未使用导入/变量**：删除或确认是否需要；属于 F 规则。
- **ruff-format 报 diff**：执行 `ruff format npusim/ tests/` 原地格式化后重新提交。
- **pytest 失败**：查看 `-v` 输出定位具体用例，区分实现错误、依赖缺失与环境限制；`skip` 表示未执行，不能当作通过或一概归为 flaky。
- **py37_compat 失败**：改用 3.7 兼容语法/API；延迟注解只影响注解求值，不能修复新版语法或标准库 API 不兼容。
- **macOS 临时目录路径断言失败**：`test_report_backends.py::ReportBackendTests::test_record_db_directory_is_the_only_report_input` 使用 `/tmp` 构造预期路径，但实际路径可能解析为 `/private/tmp`；如出现该差异，记录为环境相关失败并提交问题，不将该用例标为通过，也不通过跳过来隐去失败。
- **OAT / pre-commit 无法启动**：检查当前虚拟环境、网络和工具安装；安装失败或扫描未执行应标记为未验证，不得写通过。

## 五、编码风格约定

- 新增代码优先复用 `npusim.core` / `npusim.prof_demo` 已有工具函数，不重复造轮子。
- 日志、报错信息保持与现有模块一致的口径。
- 文档用中文（除代码标识符与术语）。
- 提交信息使用 `<type>: <描述>`，例如 `docs: 完善贡献指南`、`fix: 修复报告生成`；type 按变更选择 docs/chore/fix/feat/test 等。

---

**文件位置**：`docs/zh/development_guide.md`
