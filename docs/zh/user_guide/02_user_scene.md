# 使用场景

## kernel直调场景

展示如何使用 NPUSim 工具采集 Kernel 直调的方式来采集Ascend C算子的性能数据，以内核调用符<<<>>>方式调用算子为例。

### 前期准备

1、参考 [环境安装](../quick_start/quick_install.md)安装对应的CANN工具以及执行环境变量。
```bash
# 执行环境变量
source ${install_path}/cann/set_env.sh
```
${install_path}：表示指定安装路径，默认安装在/usr/local/Ascend目录。

2、点击 [Vector add算子样例](https://gitcode.com/cann/cann-samples/blob/master/Samples/0_Introduction/vector_add/main.asc) 获取样例工程，以Add向量加法算子为例。

```bash
# 说明：下载cann-samples仓库
git clone https://gitcode.com/cann/cann-samples.git
```


### 操作步骤

1、基于样例工程的说明，构建可运行的算子可执行文件，编译完成后，在工程目录下生成可执行文件vector_add。
```bash
# 进入项目根目录，执行如下配置命令，命令仅供参考，详细可以查看仓库的README的说明。
cmake -S . -B build -DNPU_ARCH=dav-3510
# 执行编译命令
cmake --build build --target vector_add
```

2、进入可执行文件目录，运行仿真命令。
```bash
# 进入build目录，执行如下仿真命令。
cd build/
npusim record ./vector_add -s Ascend950 -g
```

3、出现如下打屏回显，表示算子性能数据采集成功。
```bash
Vector add completed successfully!
Parallel Simulation: All threads are joined.
```

4、查看仿真报告进行性能分析，具体操作请参考《用户指南》的 [指令流水图](./01_user_guide.md/#指令流水图) 分析。


---

## PyTorch调用场景

通过 PyTorch 框架进行单算子调用的场景。

> **免责声明**：PyTorch 调用场景为尽力而为（best-effort）支持，通过向子进程注入仿真环境变量实现，覆盖范围以单进程、标准 ACL 算子下发链路为主。受 PyTorch/torch_npu 版本与仿真环境的匹配性、设备初始化、多进程（如 DataLoader）、图编译等复杂链路影响，部分场景可能调用失败；此时建议改用 [kernel直调场景](#kernel直调场景) 验证算子性能。

### 前期准备

1、参考[环境安装](../quick_start/quick_install.md)安装对应的CANN工具以及执行环境变量。
```bash
# 执行环境变量
source ${install_path}/cann/set_env.sh
```
${install_path}：表示指定安装路径，默认安装在/usr/local/Ascend目录。

2、已参考[《Ascend Extension for PyTorch 配置与安装》](https://www.hiascend.com/document/detail/zh/Pytorch/60RC3/configandinstg/instg/insg_0001.html)，完成PyTorch框架和torch_npu插件的安装。

### 操作步骤

1、准备基础的PyTorch用例test_add.py。
```python
import torch
import torch_npu
from torch_npu.testing.testcase import TestCase, run_tests

torch.npu.config.allow_internal_format = False


class TestCustomAdd(TestCase):

    def test_add_custom(self):
        length = [8, 2048]
        x = torch.rand(length, device='cpu', dtype=torch.float16)
        y = torch.rand(length, device='cpu', dtype=torch.float16)

        torch.npu.synchronize()
        output = torch.add(x.npu(), y.npu()).cpu()
        torch.npu.synchronize()

        self.assertRtolEqual(output, x + y)


if __name__ == "__main__":
    run_tests()
```

2、使用仿真命令采集性能数据。
```bash
npusim record -s Ascend950 -g "python3 test_add.py"
```

3、出现如下打屏回显，表示算子性能数据采集成功（unittest 输出以实际执行结果为准）。
```bash
Ran 1 test in 12.345s

OK
Parallel Simulation: All threads are joined.
```

4、查看仿真报告进行性能分析，具体操作请参考《用户指南》的 [指令流水图](./01_user_guide.md/#指令流水图) 分析。
