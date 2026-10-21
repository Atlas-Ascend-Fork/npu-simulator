# 快速开始

本文档基于本仓库内置的简易加法算子（vector_add），演示 npusim 工具的核心功能，帮助初学者直观体会其在算子开发过程中带来的高效性与便捷性。

## 使用前准备

本章使用的 vector_add 用例已内置在本仓库 [tests/st/Samples/0_Introduction/vector_add](../../../tests/st/Samples/0_Introduction/vector_add/main.asc) 目录，无需从外部仓库下载。

## 环境准备

请先按照[快速安装指南](./quick_install.md)完成环境准备。


## 环境变量配置

按需选择合适的命令使环境变量生效。
```bash
# 默认路径安装，以root用户为例（非root用户，将/usr/local替换为${HOME}）
source /usr/local/Ascend/cann/set_env.sh
# 指定路径安装
# source ${install_path}/cann/set_env.sh
```

## 算子编译

在本仓库根目录执行如下命令，完成 vector_add 算子的编译。

```bash
# 在本仓库根目录执行配置命令，命令仅供参考，详细可以查看仓库的README的说明。
cmake -S tests/st -B build -DNPU_ARCH=dav-3510
# 执行编译命令
cmake --build build --target vector_add
```

编译完成后，切换到可执行目录文件的所在目录`build/Samples/0_Introduction/vector_add`，可以看到算子可执行文件vector_add：

```bash
cd build/Samples/0_Introduction/vector_add
```

产物目录结构如下：

```text
build/Samples/0_Introduction/vector_add
├── vector_add                  # 算子可执行文件
├── CMakeFiles/                 # CMake 中间产物
├── cmake_install.cmake         # CMake 安装脚本
└── Makefile                    # 构建脚本
```

## 执行仿真命令

在`build/Samples/0_Introduction/vector_add`目录下运行如下仿真命令

```bash
npusim record ./vector_add -s Ascend950 -g
```

仿真工具执行日志文件在`build/Samples/0_Introduction/vector_add/npusim_*/`目录，执行日志文件为`npusim.log`。


从仿真工具日志文件可以看到示例中的打印信息：

```bash
Vector add completed successfully!
Parallel Simulation: All threads are joined.
```

## 查看性能流水

仿真性能流水文件在本项目`build/Samples/0_Introduction/vector_add/npusim_*/report/results/kernel_*/core_*`目录，流水相关文件为：

```bash
trace_core0.json
```

在Chrome浏览器中输入“chrome://tracing”地址，并将生成的指令流水图文件（trace_core0.json）拖到空白处打开，具体参数介绍参考“仿真结果解析”章节。
