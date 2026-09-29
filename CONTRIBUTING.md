# 贡献指南

本项目欢迎广大开发者体验并参与贡献。在参与社区贡献之前，请先了解行为准则、签署 CLA，并阅读源码仓的贡献流程。

## 前置条件

1. 阅读 [cann-community](https://gitcode.com/cann/community) 的社区行为准则。
2. 完成 CLA（Contributor License Agreement）协议签署。
3. 了解本仓的 Issue 与 PR 流程（见下文）。

## 提交 PR 前

1. 提交 PR 时，按 PR 模板完整填写业务背景、目的、方案等信息。
2. 若修改不是简单 bug 修复（涉及新增特性、新增接口、新增配置参数或修改代码流程），请先通过 Issue 进行方案讨论，避免代码被拒绝合入。
3. 若不确定修改是否属于「简单 bug 修复」，也请先提交 Issue 讨论。

## 常见贡献场景

### Bug 修复

1. 发现 Bug 后，按「提交 Issue / 处理 Issue 任务」指引新建 `Bug-Report|缺陷反馈` 类 Issue 描述问题。
2. 在 Issue 评论框输入 `/assign` 或 `/assign @yourself` 将 Issue 分配给自己。
3. 完成修复后提交 PR，并在 PR 中关联对应 Issue。

### 文档纠错

1. 发现文档描述错误后，新建 `Documentation|文档反馈` 类 Issue 说明问题。
2. 输入 `/assign` 或 `/assign @yourself` 认领。
3. 修正文档后提交 PR。

### 帮助解决他人 Issue

1. 在 Issue 中发表评论交流，帮助他人解决问题。
2. 若 Issue 需要代码修改，输入 `/assign` 或 `/assign @yourself` 认领并跟踪解决。

## 本地验证

提交 PR 前请在本地完成最小验证：

```bash
# 构建 wheel
bash ./clear.sh && ./build_wheel.sh

# 运行测试
pytest tests/
```

## 关联资料

- 社区行为准则与 Issue/PR 流程：[cann-community](https://gitcode.com/cann/community)
- 详细贡献场景：[贡献指南](./docs/zh/contributing_guide.md)
