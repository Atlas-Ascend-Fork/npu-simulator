<h1 align="center">NPUSim</h1>

<div align="center">
<p><b><span style="font-size:24px;">昇腾NPU调试调优仿真工具</span></b></p>
</div>

## ️ ℹ️ 简介
NPUSim（全称NPU Simulator）是一款面向算子开发场景的SoC级芯片仿真工具，用于分析运行在AI仿真器上的AI任务在各阶段的精度和性能数据（如指令执行情况等）。该工具有助于用户进行深度性能调优，使研发人员在无法获取或芯片资源紧缺的情况下，也能获得与真实芯片几乎一致的验证效果和性能反馈。

## ⚙️ 功能介绍
该工具与板上运行保持二进制兼容（同一kernel可同时在仿真和AI处理器执行），主要用途如下：

精度仿真：输出bit级精度结果，协助用户完成算子的精度验证。</br>
性能仿真：输出指令流水图，协助用户定位算子性能瓶颈问题。

## ⚡️ 环境准备

在搭建环境之前，请先确认硬件在本工具的支持范围内

* 运行环境支持的CPU 架构：aarch64、x86_64。
* 支持仿真的昇腾 AI 处理器：Ascend 950系列。

按照[快速安装指南](./docs/zh/quick_start/quick_install.md)完成环境准备。

## ⬇️ 源码下载

```bash
git clone https://gitcode.com/cann/npu-simulator.git
```

如需贡献，请先 fork 本仓库到个人账户，再从 fork 克隆并提交 PR，详见[贡献指南](#-贡献指南)。

## 🔍 目录结构
关键目录结构如下：

```text
├── docs/                                          # 项目文档目录
│   └── zh/                                        # 中文文档（快速入门、使用指南、典型案例等）
├── npusim/                                        # NPUSim 核心源码主目录
│   ├── core/                                      # 核心实现逻辑
│   │   ├── public/                                # 公共基础模块与硬件信息定义
│   │   ├── record/                                # 仿真执行录制与任务编排
│   │   └── report/                                # 性能分析报告调度与数据重组
│   ├── exclude_instr.toml                         # 默认排除/过滤指令配置
│   ├── main.py                                    # 命令行入口脚本
│   ├── prof_demo/                                 # 仿真 JSON 报告子模块
│   │   ├── analysis/                              # 指标分析（IPC、流水线利用率等）
│   │   ├── reports/                               # Chrome Tracing、Summary、VF 报告
│   │   ├── cli.py                                 # 报告命令行入口
│   │   └── schema.sql                             # 内存分析库结构
│   └── version.py                                 # 版本号定义
├── .gitcode/                                      # GitCode 工作流、模板及 CI 辅助脚本
│   └── scripts/                                   # 安全扫描、依赖审计及 CI 兼容脚本
├── tests/                                         # 自动化测试用例
│   ├── st/                                        # 算子端到端仿真测试样例（MatMul、VectorAdd 等）
│   └── test_core/                                 # 核心模块单元测试（涵盖 public、record、report、prof_demo）
├── tools/                                         # 辅助验证与分析工具脚本
├── build_wheel.sh                                 # 项目工程编译与打包脚本
├── clear.sh                                       # 编译清理脚本
├── pyproject.toml                                 # 项目与代码质量工具配置
├── setup.py                                       # Python 包构建与安装配置
└── README.md                                      # 项目说明文档
```

## 编译和安装

> NPUSim 已随 CANN 商发包发布，安装 CANN 后即可直接使用。如需使用本仓库最新代码，可按以下方式编译并覆盖安装。

详细的安装方式请参见[快速安装指南](./docs/zh/quick_start/quick_install.md)，以下为两种常见场景的简要说明。

### 方式一：CANN 包预装环境（Web IDE / 已安装 CANN）

Web IDE 等一站式开发平台已预装 CANN（含 NPUSim），如需替换为本仓库最新代码：

```bash
bash ./clear.sh && bash ./build_wheel.sh
pip3 install dist/*.whl --upgrade -t /usr/local/Ascend/python/site-packages/
```

### 方式二：开发者源码编译安装

适用于自有服务器且需手动安装 CANN 的开发者。请先下载并安装 [CANN toolkit 包](https://ascend.devcloud.huaweicloud.com/artifactory/cann-run-mirror/software/master/)（按需安装 ops 包），安装指导参考《[CANN软件安装指南](https://www.hiascend.com/document/redirect/CannCommunityInstWizard)》。

CANN 安装完成后，编译 npusim 并覆盖安装：

```bash
bash ./clear.sh && bash ./build_wheel.sh
pip3 install dist/*.whl --upgrade -t ${install_path}/python/site-packages/
```

其中 `${install_path}` 为 CANN 安装路径，root 用户默认为 `/usr/local/Ascend`。

### 验证安装

安装完成后执行以下命令验证是否生效：

```bash
npusim --help
```

## 🚀 快速入门

快速体验核心功能，请参见《[NPUSim 快速入门](./docs/zh/quick_start/quick_start.md)》。

## 📘 使用指南

工具的详细使用方法，请参见《[NPUSim 使用指南](./docs/zh/user_guide/01_user_guide.md)》。

## 💡 典型案例

NPUSim 通过一些典型案例帮助您理解并使用工具，具体案例请参见《[NPUSim 典型案例](./docs/zh/best_practices/typical_cases.md)》。

## 🛠️ 贡献指南

欢迎参与项目贡献，请参见《[贡献指南](./CONTRIBUTING.md)》。参与前请先阅读社区行为准则并签署 CLA。

代码规范与本地验证命令请参见《[代码规范与本地验证指南](./docs/zh/development_guide.md)》。

## 🏷️ 所属 SIG

本项目归属 CANN 社区 [tools SIG](https://gitcode.com/cann/community/blob/master/CANN/sigs/tools/README.md)（工具 SIG，提供 CANN 开发、调试、测试相关工具链支持）。

## ⚖️ 相关说明

🔹《[许可证声明](./LICENSE)》（Apache-2.0）
🔹《[安全声明](./SECURITY.md)》

## 💬 问题反馈

- 问题反馈：通过 GitCode [Issues](./issues) 提交。
- 社区互动：通过 GitCode [讨论](https://gitcode.com/org/cann/discussions) 参与交流。

## 🤝 建议与交流

感谢来自社区的每一个 PR，欢迎贡献！
