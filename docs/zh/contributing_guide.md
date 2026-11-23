# 贡献指南

本项目欢迎广大开发者体验并参与贡献。参与贡献前，请阅读社区[用户行为准则](https://gitcode.com/cann/community/blob/master/contributor/code-of-conduct.md)，按 [CLA 使用指南](https://gitcode.com/cann/infrastructure/blob/master/docs/cla/cla使用指南.md) 完成协议签署。

完整的贡献流程、环境搭建、CI 门禁与合入说明请以根目录《[贡献指南](../../CONTRIBUTING.md)》为准，本文档仅作场景索引。

## 贡献场景

- **贡献新特性**：新建 `Requirement|需求/特性` 类 Issue 说明背景、价值与设计方案，经 Issue 方案讨论确认后开发并提交 PR。详见[贡献新特性](../../CONTRIBUTING.md#一贡献新特性)。
- **Bug 修复**：新建 `Bug-Report|缺陷反馈` 类 Issue 描述问题，在评论框输入 `/assign` 或 `/assign @yourself` 认领后处理。详见[提交 Issue/处理 Issue 任务](https://gitcode.com/cann/community#提交issue处理issue任务)。
- **文档纠错**：新建 `Documentation|文档反馈` 类 Issue 指出文档问题并认领修正。
- **帮助解决他人 Issue**：在 Issue 中评论交流；若需代码修改，可 `/assign` 认领后跟踪解决。

## 提交 PR 前

1. 按照 [PR 模板](../../.gitcode/PULL_REQUEST_TEMPLATE.md) 完整填写业务背景、目的、方案、测试与兼容性。
2. 非简单 bug 修复（新增特性、新增接口、新增配置参数或修改代码流程）请先通过 Issue 讨论，避免代码被拒绝合入。
3. 若不确定是否属于「简单 bug 修复」，也请先提交 Issue 讨论。

## 本地验证

代码规范、自验证命令与失败排查请参见《[代码规范与本地验证指南](./development_guide.md)》。最小验证命令：

```bash
ruff check npusim/ tests/
ruff format --check --diff npusim/ tests/
python -m pytest tests/ -v --tb=short
bash .gitcode/scripts/py37_compat.sh 3.7
```

CI 门禁通过 PR 评论 `compile` 或 `/compile` 触发；门禁通过后由 Committer 检视并标注 `/lgtm`，Maintainer 标注 `/approve` 后合入。
