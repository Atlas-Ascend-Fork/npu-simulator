# 贡献指南

本项目欢迎广大开发者体验并参与贡献。参与贡献前，请阅读社区[用户行为准则](https://gitcode.com/cann/community/blob/master/contributor/code-of-conduct.md)，按 [CLA 使用指南](https://gitcode.com/cann/infrastructure/blob/master/docs/cla/cla使用指南.md) 完成协议签署，并参考 [cann-community](https://gitcode.com/cann/community) 熟悉社区贡献流程。

本仓是 NPUSim（昇腾 NPU 调试调优仿真工具）的源码仓，归属 CANN 社区 [tools SIG](https://gitcode.com/cann/community/blob/master/CANN/sigs/tools/README.md)。本指南覆盖环境搭建、各类贡献场景、特性贡献全流程、CI 门禁与合入流程。

## 贡献场景

开发者贡献场景主要包括：

- [贡献新特性](#一贡献新特性)
- [Bug 修复](#二bug-修复)
- [文档纠错](#三文档纠错)
- [帮助解决他人 Issue](#四帮助解决他人-issue)

---

## 准备工作

### 1. 获取源码

贡献代码请先 fork 本仓库到个人账户，再从个人 fork 克隆并提交 PR：

```bash
# 克隆地址请从个人 fork 仓库页面复制
git clone <your_fork_repo_url>
cd npu-simulator
git remote add upstream https://gitcode.com/cann/npu-simulator.git
```

开发前请基于最新 `master` 创建特性分支，例如 `feat/xxx`、`fix/xxx`、`docs/xxx`。

### 2. 搭建编译与运行环境

NPUSim 运行依赖 CANN 软件包，支持的运行环境为 aarch64、x86_64，支持仿真的昇腾 AI 处理器为 Ascend 950 系列。环境准备方式与完整步骤请参见《[快速安装指南](./docs/zh/quick_start/quick_install.md)》，两种常见场景如下：

**方式一：CANN 包预装环境（Web IDE / 已安装 CANN）**

```bash
bash ./clear.sh && bash ./build_wheel.sh
pip3 install dist/*.whl --upgrade -t /usr/local/Ascend/python/site-packages/
```

**方式二：开发者源码编译安装**

请先按《[CANN 软件安装指南](https://www.hiascend.com/document/redirect/CannCommunityInstWizard)》安装 CANN toolkit（按需安装 ops 包），然后在仓库根目录执行：

```bash
bash ./clear.sh && bash ./build_wheel.sh
pip3 install dist/*.whl --upgrade -t ${install_path}/python/site-packages/
```

其中 `${install_path}` 为 CANN 安装路径，root 用户默认为 `/usr/local/Ascend`。安装完成后执行 `npusim --help` 验证是否生效。

### 3. 搭建开发与自验证环境

建议在独立虚拟环境中安装开发工具，避免影响系统 Python：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r tests/requirements.txt
python -m pip install ruff==0.14.14 "pre-commit>=4.0.0" vermin
pre-commit install
```

`pre-commit install` 后，每次 `git commit` 会自动执行代码规范与合规检查。工具的完整说明、代码规范与失败排查请参见《[代码规范与本地验证指南](./docs/zh/development_guide.md)》。

### 4. 提交 PR 前须知

1. 提交 PR 时，请按 [PR 模板](./.gitcode/PULL_REQUEST_TEMPLATE.md) 完整填写业务背景、目的、方案、测试与兼容性等信息。
2. 若您的修改涉及新增特性、新增接口、新增配置参数或修改代码流程等（非简单 bug 修复），请务必先通过 Issue 进行方案讨论，避免代码被拒绝合入。
3. 若您不确定修改是否属于「简单 bug 修复」，也请先提交 Issue 进行讨论。
4. 提交前请确认已签署 CLA，且提交内容不含密钥、个人信息或未脱敏数据。

---

## 一、贡献新特性

如果您希望基于 NPUSim 新增能力（例如新增命令行参数、新增分析指标或报告字段、新增指令支持、扩展仿真场景等），欢迎在 Issue 中提出您的想法与设计方案。完整的贡献过程如下。

### 1. 创建 Issue 需求

新建 `Requirement|需求/特性` 类 Issue，阐明新特性的设计方案。Issue 一般需包含以下内容：

- **背景信息**：当前存在的痛点或使用场景。
- **价值/作用**：特性带来的收益，面向的用户群体。
- **设计方案**：功能定义、输入输出、涉及模块与目录、对外接口或 CLI 参数变化。
- **兼容性影响**：对既有 CLI 参数、报告字段、SQLite schema、Python 3.7 支持的影响。

具体操作步骤请参阅：[Issue 操作指南](https://gitcode.com/cann/community/blob/master/contributor/issue-operation.md)。

### 2. 方案讨论

创建需求类 Issue 后，由本仓 Maintainer/Committer 在 Issue 中参与方案讨论并给出结论。

**方案确认：** 请按讨论结论确定实现范围与代码落点；若讨论中需要调整设计，先在 Issue 中同步结论再修改代码。

**跨项目需求：** 若需求涉及跨仓库接口、社区公共能力或技术路线调整，可按需提请对应 SIG 讨论，会议信息见 [CANN SIG 列表](https://gitcode.com/cann/community/blob/master/CANN/sigs/README.md)。

### 3. 开发与本地验证

- 新增代码优先复用 `npusim.core`、`npusim.prof_demo` 中已有工具函数，不重复造轮子。
- 遵循模块现有分层：核心仿真逻辑位于 `npusim/core`，报告与分析逻辑位于 `npusim/prof_demo`，命令行入口位于 `npusim/main.py`。
- 代码必须兼容 Python 3.7，禁止使用海象运算符、`match` 语句、`str.removeprefix` 等新版本语法；详见《[代码规范与本地验证指南](./docs/zh/development_guide.md#2-python-37-兼容重要)》。
- 测试分两层：纯逻辑/工具函数补 UT（`tests/test_core/`、`tests/test_prof/`）；涉及仿真链路、指令执行、报告产物的改动，优先补 ST 样例（`tests/st/Samples`，含 `main.asc`、`CMakeLists.txt` 与校验脚本），确保 PreSmoke 可覆盖。

**常见特性落点（按改动类型自查）：**

- 新增 CLI 参数：`npusim/main.py` 参数定义、`npusim/core/record/record.py` 参数实现，并同步更新 `docs/zh/` 使用说明。
- 新增分析指标/报告字段：`npusim/prof_demo` 下的报告策略与数据库字段，确认报告产物与使用文档一致。
- 新增指令支持：检查 `npusim/exclude_instr.toml`、`npusim/core/public/soc_info.py`、`npusim/core/public/typical_operator_times.py` 是否需要同步。
- 新增仿真场景：补充 `tests/st/Samples` 样例，确认 PreSmoke 端到端链路可覆盖。

开发完成后，请在本地执行以下最小验证（完整命令见[附录 B](#b-本地验证命令速查)）：

```bash
ruff check npusim/ tests/
ruff format --check --diff npusim/ tests/
python -m pytest tests/ -v --tb=short
bash .gitcode/scripts/py37_compat.sh 3.7
```

### 4. PR 提交

#### 交付件要求

| 类型 | 要求 | 参考资料 |
| --- | --- | --- |
| 代码交付件 | 提供特性实现代码，遵循现有模块分层与编码风格 | [代码规范](./docs/zh/development_guide.md) |
| 测试交付件 | 按改动类型补充 UT（`tests/test_core/`、`tests/test_prof/`）或 ST 样例（`tests/st/Samples`）；仿真链路、指令、报告产物变更需补 ST，并确认 PreSmoke 通过 | [tests/st/Samples](./tests/st/Samples)、[tests/](./tests/) |
| 文档交付件 | 涉及 CLI 参数、配置、报告字段或使用方式变化时，更新 `docs/zh/` 或对应模块 `README.md`；纯内部重构需说明不更新的原因 | [docs/zh/](./docs/zh/) |
| 兼容性说明 | 说明 Python 版本、CLI 参数、DB/schema、报告字段的兼容性影响 | [PR 模板](./.gitcode/PULL_REQUEST_TEMPLATE.md) |

#### 合规检查

- [ ] 代码是否符合本仓 `ruff` 规范与 Python 3.7 兼容要求。
- [ ] 代码是否可以正常编译打包（`bash ./build_wheel.sh`）。
- [ ] 新增或修改行为是否已补充测试并通过。
- [ ] Markdown 文档语法与链接是否正确。

#### 提交规范

- **提交信息**：使用 `<type>: <描述>`，例如 `feat: 新增 xxx 指标`、`fix: 修复报告生成失败`、`docs: 完善贡献指南`。
- **PR 提交**：向 `cann/npu-simulator` 的 `master` 分支提交 PR，检查 PR 标题是否清晰、描述是否规范（指明改动内容和原因、是否关联对应 Issue）。
- **关联 Issue**：在 PR 中填写「关联 Issue」，并使用 `Related issue: #<issue编号>` 显式关联。

### 5. CI 门禁

PR 创建后，在 PR 评论框中输入 `compile` 或 `/compile` 指令触发开源仓门禁，并依据 CI 检测结果进行修改。目前门禁包含以下检查项：

- **PreBuild**：构建镜像与预检环境准备。
- **CodeCheck**：代码静态检查（`pre-commit` 钩子，含 ruff、codespell、OAT、bandit、pip-audit 等）、x86_64/ubuntu24 与 aarch64/ubuntu24 编译打包、Python 3.7 兼容检查、安全扫描。
- **UT**：`tests/` 下全量单元测试与覆盖率统计。
- **PreSmoke**：x86 与 ARM 冒烟测试（目标分支为 `master` 时执行）。

若为 codecheck 误报，请提交给本仓 Maintainer/Committer 确认后屏蔽。门禁通过后，请在关联的 Issue 中 @ Committer。若门禁失败，请按机器人评论中的失败任务日志/产物入口定位原因；修改后推送到同一源分支，再按机器人提示重新触发检查，确认结果对应最新一次提交。

门禁与合规状态会体现在 PR 标签上：`ci-pipeline-passed` 表示最新门禁通过，`cann-cla/yes` 表示 CLA 检查通过，`stat/needs-squash` 表示合入时需使用 squash 方式。

门禁检查项的详细说明见[附录 A](#a-ci-门禁检查项)。

### 6. Committer 检视

Committer 检视后将反馈检视意见，请根据意见修改，完成后 @ Committer。新增提交后需重新检查门禁结果。

### 7. Maintainer 合入

Committer 检视通过后，将标注 `/lgtm` 标签。Maintainer 最终审核，确认无问题后，将标注 `/approve` 标签合入 PR。

**合入方式：** 仓库标注 `stat/needs-squash` 时，请使用 squash 合入，保持 `master` 提交历史线性；开发者不要直接向 `master` 推送。

**合入后：** 删除源分支，确认关联 Issue 已关闭（或由 PR 自动关闭）。

合入前请确认：

- [ ] 所有门禁检查项均通过，且结果对应最新提交。
- [ ] 已获得 Committer 的 `/lgtm` 与 Maintainer 的 `/approve`。
- [ ] PR 已关联对应 Issue，且 Issue 中的方案讨论已形成结论。

---

## 二、Bug 修复

如果您在本项目中发现了 Bug，希望对其进行修复，欢迎您新建 Issue 进行反馈和跟踪处理。

您可以按照 [提交 Issue/处理 Issue 任务](https://gitcode.com/cann/community#提交issue处理issue任务) 指引新建 `Bug-Report|缺陷反馈` 类 Issue 对 Bug 进行描述，然后在评论框中输入 `/assign` 或 `/assign @yourself`，将该 Issue 分配给您进行处理。

修复完成后提交 PR，并在 PR 中关联对应 Issue。若 Bug 修复涉及代码流程调整或行为变化，同样需补充测试与文档说明。

---

## 三、文档纠错

如果您在本项目中发现文档描述错误，欢迎您新建 Issue 进行反馈和修复。

您可以按照 [提交 Issue/处理 Issue 任务](https://gitcode.com/cann/community#提交issue处理issue任务) 指引新建 `Documentation|文档反馈` 类 Issue 指出对应文档的问题，然后在评论框中输入 `/assign` 或 `/assign @yourself`，将该 Issue 分配给您纠正对应文档描述。

文档修改在 PR 中需说明调整内容，并核对文中的相对链接、命令与代码入口是否真实存在。

---

## 四、帮助解决他人 Issue

如果社区中他人遇到的问题您有合适的解决方法，欢迎您在 Issue 中发表评论交流，帮助他人解决问题和痛点，共同优化易用性。

如果对应 Issue 需要进行代码修改，您可以在 Issue 评论框中输入 `/assign` 或 `/assign @yourself`，将该 Issue 分配给您，跟踪协助解决问题。

---

## 附录

### A. CI 门禁检查项

门禁由 `.gitcode/workflows/npu-simulator_action.yml` 编排，可通过 PR 评论 `compile` 或 `/compile` 触发：

| 阶段 | 检查项 | 说明 |
| --- | --- | --- |
| PreBuild | 镜像与预检 | 准备各检查任务的构建镜像，完成预检 |
| CodeCheck | codecheck | 执行 `pre-commit` 静态检查（ruff、ruff-format、codespell、OAT、bandit、pip-audit 等） |
| CodeCheck | 编译（x86_64 / aarch64） | 在 ubuntu24 环境分别构建 wheel 包 |
| CodeCheck | py37_compat | 检查代码是否兼容 Python 3.7 |
| CodeCheck | scan | 安全扫描（含密钥/敏感信息检查） |
| UT | UT_Test | 执行 `tests/` 全量单元测试并生成覆盖率报告 |
| PreSmoke | PreSmoke_x86 / PreSmoke_arm | 目标分支为 `master` 时执行端到端冒烟测试 |

### B. 本地验证命令速查

```bash
# 1. Lint
ruff check npusim/ tests/

# 2. 格式化检查
ruff format --check --diff npusim/ tests/

# 3. 单元测试（含覆盖率）
python -m pytest tests/ -v --tb=short --cov=npusim --cov-report=term

# 4. Python 3.7 兼容检查
bash .gitcode/scripts/py37_compat.sh 3.7

# 5. 预提交钩子（含 OAT 开源合规检查，需联网）
pre-commit run --all-files

# 6. ST 样例编译（需 toolkit 与 bisheng；NPU_ARCH 取 dav-3510 或 dav-2201）
cmake -S tests/st -B build/st-samples -DNPU_ARCH=dav-3510
cmake --build build/st-samples --target <样例 target>

# 7. 密钥与敏感信息扫描
python3 .gitcode/scripts/secret_scan.py .

# 8. 构建 wheel
bash ./clear.sh && bash ./build_wheel.sh
```

说明：`skip` 表示未执行，不能当作通过；未能执行的检查（如缺少 CANN 环境导致的真实链路验证、网络受限导致的 OAT 未运行）应在 PR 中标记为未验证并说明原因，不以静态检查代替平台验收。

### C. PR 提交自查清单

- [ ] 已阅读贡献指南并按社区要求签署 CLA。
- [ ] 新特性已完成 Issue 方案讨论并关联对应 Issue。
- [ ] 已补充或更新测试，并在 PR 中记录实际执行的命令、环境与结果。
- [ ] 涉及 CLI 参数、配置、报告字段变化时已同步更新文档。
- [ ] 已说明 Python 3.7、CLI、DB/schema、报告字段的兼容性影响。
- [ ] 已检查提交内容不含密钥、个人信息或未脱敏数据。
- [ ] 已处理或逐条答复评审意见；新增提交后重新检查门禁结果。

### D. 关联资料

- 社区行为准则、Issue/PR 操作指引：[cann-community](https://gitcode.com/cann/community)
  - 用户行为准则：[code-of-conduct.md](https://gitcode.com/cann/community/blob/master/contributor/code-of-conduct.md)
  - CLA 签署：[CLA 使用指南](https://gitcode.com/cann/infrastructure/blob/master/docs/cla/cla使用指南.md)
  - 社区角色定义及晋升机制：[role-definition-and-promotion-mechanism.md](https://gitcode.com/cann/community/blob/master/governance/role-definition-and-promotion-mechanism.md)
- 社区 SIG 信息（跨项目协调时参考）：[CANN SIG 列表](https://gitcode.com/cann/community/blob/master/CANN/sigs/README.md)
- 代码规范与本地验证：[代码规范与本地验证指南](./docs/zh/development_guide.md)
- 环境准备：[快速安装指南](./docs/zh/quick_start/quick_install.md)
- PR 填写模板：[PULL_REQUEST_TEMPLATE.md](./.gitcode/PULL_REQUEST_TEMPLATE.md)
