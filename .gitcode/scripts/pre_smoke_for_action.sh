#!/bin/bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# GitCode Actions 流水线的 smoke 测试。
#
# 当前流水线使用本地 tests/st 样例：整条链路只依赖 toolkit 包，不下载 ops 包、
# 不解压 aclnn 头文件，也不构造 merged opp 目录树。
#
# 流程：
#   1. 安装 CANN toolkit（优先复用镜像预装，否则从镜像站下载）
#   2. 编译 tests/st 中的本地 ST 样例
#   3. 用 build 阶段产出的 wheel 覆盖 toolkit 自带的 npusim
#   4. 串行执行各样例仿真，逐个校验
#   5. 无论成功失败，统一打包仿真产物供下载
#
# 只需要 toolkit 包。ST 样例通过 ASCEND_HOME_PATH 解析依赖，并用 toolkit
# 自带的 bisheng 编译 .asc 源文件（见 cmake/ascend.cmake），因此不下载 ops 包、
# 不解压 aclnn 头文件、也不构造 merged opp 目录树。

set -uo pipefail

WORKSPACE="${WORKSPACE:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "${WORKSPACE}"

ARCH=$(uname -m)
# dav-3510 是 ST 样例 CMake 认可的 Ascend950 NPU 架构名。
NPU_ARCH="${NPU_ARCH:-dav-3510}"
SOC_VERSION="${SOC_VERSION:-Ascend950}"
CANN_INSTALL_PATH="${CANN_INSTALL_PATH:-${HOME}/Ascend}"
CANN_MIRROR_API="https://ascend.devcloud.huaweicloud.com/artifactory/api/storage/cann-run-mirror/software/master"
CANN_MIRROR_DL="https://ascend.devcloud.huaweicloud.com/artifactory/cann-run-mirror/software/master"
SAMPLES_DIR="tests/st"
SAMPLES_BUILD_DIR="${WORKSPACE}/build/st-samples"

# 所有仿真产物统一落在这个目录下，最后整体打包。
SMOKE_OUT="${WORKSPACE}/smoke_logs"
SIM_ROOT="${SMOKE_OUT}/simulations"

# 仿真并发数，默认串行。
#
# npusim 自身就是多线程的（每个 die 一个线程，日志可见 die 0-3 并行），单个实例
# 已经吃满可用核心，再并发多个实例只会过度订阅 CPU。实测代价极大：
#
#   并行度 3（run #12）          串行（run #10）
#   matmul                505s   34s
#   rms_norm_quant_6      515s   -
#   vf_profiling          444s   -
#
# 从 matmul 的内部时间戳能看出问题所在：4 个 die 的硬件仿真各约 10s，第 18s 就
# 都算完了，但 "All threads are joined" 直到 8 分 25 秒才出现——8 分钟耗在线程
# 同步上，而单独跑时该行是 00:29.6。
#
# 若要并行，正确做法是拆成多个 CI job，各自独占 runner，而不是在一台机器上并发。
SIM_JOBS="${SIM_JOBS:-1}"

# 待测样例，每行 7 个字段，以 | 分隔：
#   构建target|安装子路径|可执行文件名|运行参数|校验方式|前置命令|后置命令
#
# 前置命令：仿真前在样例工作目录执行（用于需要外部生成数据的样例；matmul 与
#           rms_norm_quant 由程序内部自行调用 gen_data.py，此处留空）。
# 后置命令：仿真后在样例工作目录执行（用于需要外部比对的样例）。
#
# 校验方式：
#   verify_pass  - 日志含 "[PASS] NPU results are consistent with CPU"
#   diff_zero    - 日志含 "Compare Difference length 0"（程序内部自比对；这类
#                  样例即使精度不符也返回 0，只能靠日志判定）
#   test_pass    - 日志含 "test pass"（由后置的 verify_result.py 打印）
#   vec_add_pass - 日志含 "Vector add completed successfully!"（vector_add 内部 CPU 基准校验）
#   none         - 样例本身不做数值校验，仅判定退出码与产物
SAMPLE_CASES=(
    # 小 shape bfloat16 matmul，程序内部调用 gen_data.py / verify_result.py
    "matmul|0_Introduction/matmul|matmul|256 256 256|verify_pass||"

    # 快速开始入门样例：fp32 向量加，内部 CPU 基准校验，精度不符时返回 1。
    # 元素个数由 main.asc 内部固定为 409600，无需外部传参。
    "vector_add|0_Introduction/vector_add|vector_add||vec_add_pass||"

    # rms_norm_quant 的最终优化版本（binary sum）。shape 由 main.asc 内部固定为
    # a=256 r=512，并由程序自己调用 gen_data.py 生成数据，无需外部传参。
    "rms_norm_quant_6_binary_sum|2_Performance/rms_norm_quant_story|rms_norm_quant_6_binary_sum||diff_zero||"

    # 第三章：基于仿真的 VF profiling（warmup / pure_vf IPC），无输入无校验
    "simulation_based_vf_profiling|3_Utilities/simulation-based-vf-profiling|simulation_based_vf_profiling||none||"

    # MatMul+ReLU 的 AIC/AIV 混合场景。数据生成与结果比对都要外部执行：
    # gen_data.py 先产出 input/golden，跑完再用 verify_result.py 比对，成功打印
    # "test pass!"（见样例 README 的"运行与校验"一节）。
    "cv_datapath_2_mix_scenario2|1_Features/hardware_features/cv_datapath|cv_datapath_2_mix_scenario2||test_pass|python3 gen_data.py|python3 verify_result.py output/output.bin output/golden.bin"

    # HiF8 量化，TOTAL_LENGTH=2048 已足够小，无需传参。同样需要外部生成与比对。
    "quantize_hif8_demo|1_Features/hardware_features/hif8|quantize_hif8_demo||test_pass|python3 gen_data.py|python3 verify_result.py output/output_y.bin output/golden_y.bin"

    # GM→UB 纯读带宽。不传参会扫完整的默认 queBytes/bufNum 列表，仿真下太慢，
    # 因此只跑单组（16KB tile、buffer 数 4）。该样例只测带宽，无精度校验。
    "bw_read|1_Features/hardware_features/mem_bandwidth|bw_read|16384 4|none||"
)

log() {
    echo "===================================================================="
    echo "$(date '+%Y%m%d.%H%M%S') : $*"
    echo "===================================================================="
}

# 无论成功、失败还是部分失败都打包产物：里面本身就含错误日志，是排查依据。
package_artifacts() {
    if [ ! -d "${SIM_ROOT}" ]; then
        return 0
    fi
    log "打包仿真产物"
    local tarball="${SMOKE_OUT}/simulations.tar.gz"
    if tar -zcf "${tarball}" -C "${SMOKE_OUT}" simulations 2>/dev/null; then
        echo "已打包：${tarball} ($(du -h "${tarball}" | cut -f1))"
    else
        echo "WARNING: 打包 ${SIM_ROOT} 失败"
    fi
    # driver 侧日志与仿真日志分开存放，一并收集。
    if [ -d "${HOME}/ascend/log" ]; then
        tar -zcf "${SMOKE_OUT}/slog.tar.gz" -C "${HOME}/ascend" log 2>/dev/null \
            && echo "已打包：${SMOKE_OUT}/slog.tar.gz"
    fi
}
# 任何原因退出都先打包，确保产物可下载。
trap package_artifacts EXIT

PIP_FLAGS=""
if pip3 install --help 2>&1 | grep -q -- '--break-system-packages'; then
    PIP_FLAGS="--break-system-packages"
fi
# 190MB 级别的 wheel 在 CI 网络下容易超时，显式给足重试与超时。
PIP_NET_FLAGS="--retries 5 --timeout 120"

# 某些镜像不把 console script 目录加入 PATH（例如 pip 安装的 cmake）。
_PY_SCRIPTS=$(python3 -c "import sysconfig; print(sysconfig.get_path('scripts'))")
case ":${PATH}:" in
    *:"${_PY_SCRIPTS}":*) ;;
    *) export PATH="${_PY_SCRIPTS}:${PATH}" ;;
esac

mkdir -p "${SIM_ROOT}"

# ==============================
# 1. CANN toolkit
# ==============================
log "步骤 1：检查 CANN toolkit（arch=${ARCH}）"

CANN_PREINSTALLED=false

# 若镜像里已预装 toolkit（例如 ci_cann），直接复用。
for _set_env in \
    "/home/jenkins/Ascend/ascend-toolkit/set_env.sh" \
    "/usr/local/Ascend/ascend-toolkit/set_env.sh" \
    "${CANN_INSTALL_PATH}/ascend-toolkit/set_env.sh"; do
    if [ -f "${_set_env}" ]; then
        set +u
        # shellcheck disable=SC1090
        source "${_set_env}"
        set -u
        CANN_PREINSTALLED=true
        echo "已从 ${_set_env} 载入 CANN 环境"
        break
    fi
done

if [ "${CANN_PREINSTALLED}" = "false" ] && [ -n "${ASCEND_TOOLKIT_HOME:-}" ] \
        && [ -d "${ASCEND_TOOLKIT_HOME}" ]; then
    CANN_PREINSTALLED=true
    echo "通过 ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME} 找到 toolkit"
fi

if [ "${CANN_PREINSTALLED}" = "true" ]; then
    echo "使用预装的 CANN toolkit，跳过下载"
else
    echo "未发现预装 CANN，从镜像站解析最新版本..."

    LATEST_VERSION=$(python3 - "${CANN_MIRROR_API}" <<'PYEOF'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=60) as resp:
        data = json.loads(resp.read())
except Exception as exc:                      # noqa: BLE001 - 打印后由外层判空失败
    print("", end="")
    sys.stderr.write(f"查询镜像站失败: {exc}\n")
    sys.exit(0)

versions = [c["uri"].strip("/") for c in data.get("children", []) if c.get("folder")]
versions.sort(key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
print(versions[-1] if versions else "", end="")
PYEOF
)

    if [ -z "${LATEST_VERSION}" ]; then
        echo "ERROR: 无法从 ${CANN_MIRROR_API} 确定最新 CANN 版本"
        exit 1
    fi
    echo "最新 CANN 版本：${LATEST_VERSION}"

    TOOLKIT_PKG=$(python3 - "${CANN_MIRROR_API}/${LATEST_VERSION}" "${ARCH}" <<'PYEOF'
import json
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=60) as resp:
    data = json.loads(resp.read())

for child in data.get("children", []):
    name = child["uri"].strip("/")
    if "toolkit" in name and sys.argv[2] in name and name.endswith(".run"):
        print(name, end="")
        break
PYEOF
)

    if [ -z "${TOOLKIT_PKG}" ]; then
        echo "ERROR: ${LATEST_VERSION} 中未找到 arch=${ARCH} 的 toolkit 包"
        exit 1
    fi

    echo "下载 toolkit：${TOOLKIT_PKG}"
    if ! wget -nv "${CANN_MIRROR_DL}/${LATEST_VERSION}/${TOOLKIT_PKG}"; then
        echo "ERROR: 下载 ${TOOLKIT_PKG} 失败"
        exit 1
    fi

    chmod +x "${TOOLKIT_PKG}"
    if ! ./"${TOOLKIT_PKG}" --install --quiet --install-path="${CANN_INSTALL_PATH}"; then
        echo "ERROR: toolkit 安装失败"
        exit 1
    fi

    set +u
    for _set_env in \
        "${CANN_INSTALL_PATH}/ascend-toolkit/set_env.sh" \
        "${CANN_INSTALL_PATH}/set_env.sh"; do
        if [ -f "${_set_env}" ]; then
            echo "从 ${_set_env} 载入 CANN 环境"
            # shellcheck disable=SC1090
            source "${_set_env}"
            break
        fi
    done
    set -u
fi

# 样例的 cmake/ascend.cmake 读 ASCEND_HOME_PATH，npusim 读
# ASCEND_TOOLKIT_HOME，两者指向同一棵树。
: "${ASCEND_TOOLKIT_HOME:=${ASCEND_HOME_PATH:-${CANN_INSTALL_PATH}/ascend-toolkit/latest}}"
: "${ASCEND_HOME_PATH:=${ASCEND_TOOLKIT_HOME}}"
export ASCEND_TOOLKIT_HOME ASCEND_HOME_PATH

if [ ! -d "${ASCEND_TOOLKIT_HOME}" ]; then
    echo "ERROR: ASCEND_TOOLKIT_HOME 不存在：${ASCEND_TOOLKIT_HOME}"
    exit 1
fi

echo "ASCEND_TOOLKIT_HOME=${ASCEND_TOOLKIT_HOME}"
echo "ASCEND_HOME_PATH=${ASCEND_HOME_PATH}"

# 样例由 toolkit 自带的 bisheng 编译，缺失则尽早失败。
BISHENG="${ASCEND_TOOLKIT_HOME}/${ARCH}-linux/ccec_compiler/bin/bisheng"
if [ ! -x "${BISHENG}" ]; then
    echo "ERROR: 未找到 bisheng 编译器：${BISHENG}"
    exit 1
fi
echo "bisheng：${BISHENG}"

# ==============================
# 2. 编译本地 ST 样例
# ==============================
log "步骤 2：编译 ${SAMPLES_DIR}（NPU_ARCH=${NPU_ARCH}）"

if ! command -v cmake >/dev/null 2>&1; then
    echo "未找到 cmake，开始安装..."
    pip3 install ${PIP_FLAGS} ${PIP_NET_FLAGS} cmake || {
        echo "ERROR: 安装 cmake 失败"
        exit 1
    }
fi
cmake --version | head -1

if [ ! -f "${WORKSPACE}/${SAMPLES_DIR}/CMakeLists.txt" ]; then
    echo "ERROR: 未找到本地 ST 样例：${WORKSPACE}/${SAMPLES_DIR}/CMakeLists.txt"
    exit 1
fi

# 各样例的 gen_data.py / verify_result.py 依赖以下包：
# matmul 用 numpy + torch + ml_dtypes，hif8 用 en_dtypes（hifloat8 类型），
# rms_norm_quant 只用 numpy。
#
# torch 必须是 +cpu 变体：PyPI 镜像（华为云、清华）上的都是 GPU 版，import 时
# 会硬找 libcudart.so.13 直接崩，加 --no-deps 也无济于事。而 +cpu 是 PyTorch
# 自建索引的 local version，不进 PyPI，所以任何 PyPI 镜像都没有。
#
# 又不能走 pytorch 官方索引：download.pytorch.org 会重定向到
# download-r2.pytorch.org，CI runner 到该主机的链路不通（run #8 超时、
# run #11 卡死在 191.8MB 的下载上）。
#
# 阿里云镜像了 pytorch-wheels/cpu，直连不重定向，因此按完整 URL 安装。
# 版本取 2.6.0+cpu，与本地验证通过的环境一致。
#
# wheel 文件名带 ABI 标签，必须与当前解释器匹配，因此按 python3 的版本和架构
# 动态拼接，不能写死——不同流水线的 runner 解释器版本不一致（本仓 Actions 是
# 3.12，另一条 Jenkins 流水线是 3.10）。
# 该镜像提供 cp39~cp313；x86_64 的命名是 linux_x86_64，aarch64 是
# manylinux_2_28_aarch64。
TORCH_VERSION="${TORCH_VERSION:-2.6.0}"
TORCH_MIRROR="${TORCH_MIRROR:-https://mirrors.aliyun.com/pytorch-wheels/cpu}"

_PY_TAG=$(python3 -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')")
case "${ARCH}" in
    x86_64)  _WHL_PLAT="linux_x86_64" ;;
    aarch64) _WHL_PLAT="manylinux_2_28_aarch64" ;;
    *)
        echo "ERROR: 该镜像未提供 ${ARCH} 架构的 CPU 版 torch wheel"
        echo "       可用列表：${TORCH_MIRROR}/"
        echo "       可通过 TORCH_WHEEL_URL 显式指定"
        exit 1
        ;;
esac

# %2B 是 URL 编码的 '+'（本地版本号 2.6.0+cpu）。
TORCH_WHEEL_URL="${TORCH_WHEEL_URL:-${TORCH_MIRROR}/torch-${TORCH_VERSION}%2Bcpu-${_PY_TAG}-${_PY_TAG}-${_WHL_PLAT}.whl}"
echo "解释器 $(python3 -V 2>&1) → ABI ${_PY_TAG}，架构 ${ARCH}"

# 提前确认该 wheel 存在，否则 pip 只会报一句难以定位的 404。
# curl / wget 未必都装，用 python3 兜底（它必然存在，前面已在用）。
_wheel_exists() {
    if command -v curl >/dev/null 2>&1; then
        curl -sfI --retry 2 --max-time 60 "$1" >/dev/null 2>&1
    elif command -v wget >/dev/null 2>&1; then
        wget -q --spider --tries=2 --timeout=60 "$1" >/dev/null 2>&1
    else
        python3 - "$1" <<'PYEOF'
import sys, urllib.request
req = urllib.request.Request(sys.argv[1], method="HEAD")
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
PYEOF
    fi
}

if ! _wheel_exists "${TORCH_WHEEL_URL}"; then
    echo "ERROR: 镜像上没有匹配当前解释器的 torch wheel"
    echo "       期望：${TORCH_WHEEL_URL}"
    echo "       可用列表：${TORCH_MIRROR}/"
    echo "       可通过 TORCH_WHEEL_URL 显式指定其它 wheel"
    exit 1
fi

echo "安装数据生成依赖（CPU 版 torch）..."
# 间接依赖（sympy、networkx、fsspec 等）从华为云镜像取，避免 pip 回落到
# files.pythonhosted.org 而超时。
if ! pip3 install ${PIP_FLAGS} ${PIP_NET_FLAGS} \
        -i https://mirrors.huaweicloud.com/repository/pypi/simple \
        "${TORCH_WHEEL_URL}"; then
    echo "ERROR: 安装 CPU 版 torch 失败：${TORCH_WHEEL_URL}"
    exit 1
fi
if ! pip3 install ${PIP_FLAGS} ${PIP_NET_FLAGS} \
        -i https://mirrors.huaweicloud.com/repository/pypi/simple \
        "numpy>=2.0,<3" "ml_dtypes==0.5.4" "en_dtypes==0.0.4"; then
    echo "ERROR: 安装 numpy / ml_dtypes / en_dtypes 失败"
    exit 1
fi

# 数据生成脚本的依赖尽早验证，避免拖到前置命令阶段才失败（run #15 即因缺
# en_dtypes 导致 hif8 的 gen_data.py 失败）。
python3 -c "
import numpy, ml_dtypes, en_dtypes
print('numpy', numpy.__version__, '| ml_dtypes', ml_dtypes.__version__, '| en_dtypes OK')
" || {
    echo "ERROR: 数据生成依赖导入失败"
    exit 1
}

# torch 装的是 CPU 变体、且能正常 import，尽早验证，避免拖到仿真阶段才失败。
python3 -c "
import os
os.environ['TORCH_DEVICE_BACKEND_AUTOLOAD'] = '0'
import torch
print('torch', torch.__version__)
assert 'cpu' in torch.__version__, f'expected a +cpu build, got {torch.__version__}'
" || {
    echo "ERROR: torch 无法导入或不是 CPU 变体"
    exit 1
}

BUILD_LOG="${SMOKE_OUT}/samples_build.log"

# 直接 configure + build，不走 .ci/build.sh：那个脚本还会打 zip 并编译全部样例。
if ! cmake -S "${WORKSPACE}/${SAMPLES_DIR}" -B "${SAMPLES_BUILD_DIR}" \
        -DNPU_ARCH="${NPU_ARCH}" 2>&1 | tee "${BUILD_LOG}"; then
    echo "ERROR: cmake configure 失败"
    exit 1
fi

# 只编待测 target。全量编译 83 个 dav-3510 样例耗时长得多，而且会牵扯到无关
# 样例的数据生成脚本（曾因此失败）。
BUILD_TARGETS=()
for _case in "${SAMPLE_CASES[@]}"; do
    IFS='|' read -r _target _ _ _ _ _ _ <<< "${_case}"
    BUILD_TARGETS+=("--target" "${_target}")
done

echo "编译 target：${BUILD_TARGETS[*]}"
if ! cmake --build "${SAMPLES_BUILD_DIR}" "${BUILD_TARGETS[@]}" --parallel 2>&1 | tee -a "${BUILD_LOG}"; then
    echo "ERROR: 编译样例失败"
    exit 1
fi

# 校验产物，并把运行期需要的脚本放到可执行文件旁边。
for _case in "${SAMPLE_CASES[@]}"; do
    IFS='|' read -r _target _path _bin _ _ _ _ <<< "${_case}"
    # CMake 在 build/ 下镜像源码目录结构，所以是 build/st-samples/Samples/<path>。
    _dir="${SAMPLES_BUILD_DIR}/Samples/${_path}"
    if [ ! -x "${_dir}/${_bin}" ]; then
        echo "ERROR: 未生成可执行文件 ${_dir}/${_bin}"
        exit 1
    fi
    # 可执行文件运行时通过 system() 调用同目录下的 python 脚本；我们跳过了
    # cmake --install，所以手动拷过去（部分样例由 configure_file 复制，
    # 已存在则不会有影响）。
    if [ -d "${WORKSPACE}/${SAMPLES_DIR}/Samples/${_path}/scripts" ]; then
        cp -n "${WORKSPACE}/${SAMPLES_DIR}/Samples/${_path}/scripts"/* "${_dir}/" 2>/dev/null || true
    fi
    echo "已编译：${_dir}/${_bin}"
done

# ==============================
# 3. 用 wheel 覆盖 toolkit 自带的 npusim
# ==============================
log "步骤 3：安装 npusim wheel 到 toolkit"

# toolkit 自带一份 cannsim/npusim 在 python/site-packages 下，而 set_env.sh 会把
# 该目录前置到 PYTHONPATH。若装到系统 site-packages，toolkit 那份会始终遮蔽我们
# 构建的 wheel，smoke 跑绿也证明不了被测代码。所以用 -t 直接装进同一目录覆盖它。
TOOLKIT_SITE_PACKAGES="${ASCEND_TOOLKIT_HOME}/python/site-packages"
if [ ! -d "${TOOLKIT_SITE_PACKAGES}" ]; then
    echo "ERROR: 未找到 toolkit site-packages：${TOOLKIT_SITE_PACKAGES}"
    exit 1
fi

WHEEL=$(ls "${WORKSPACE}"/dist/*.whl 2>/dev/null | head -1)
if [ -z "${WHEEL}" ]; then
    echo "ERROR: dist/ 下没有 wheel"
    exit 1
fi
echo "wheel：${WHEEL}"

if ! pip3 install ${PIP_FLAGS} ${PIP_NET_FLAGS} "${WHEEL}" --upgrade -t "${TOOLKIT_SITE_PACKAGES}"; then
    echo "ERROR: 安装 ${WHEEL} 到 ${TOOLKIT_SITE_PACKAGES} 失败"
    exit 1
fi

# 确认真正被 import 的就是刚装的这份。
# 注意必须离开仓库根目录再检查：npu-simulator 仓库根下就有 npusim/ 包，而 CWD
# 在 sys.path 中优先于 PYTHONPATH，在仓库根下检查会解析到源码目录而非 wheel。
NPUSIM_FILE=$(cd "${SIM_ROOT}" && python3 -c "import npusim; print(npusim.__file__)")
echo "npusim 实际解析到：${NPUSIM_FILE}"
case "${NPUSIM_FILE}" in
    "${TOOLKIT_SITE_PACKAGES}"/*)
        echo "  OK：解析到我们覆盖的 toolkit 目录"
        ;;
    *)
        echo "ERROR: npusim 解析到 ${TOOLKIT_SITE_PACKAGES} 之外，"
        echo "       本次 smoke 校验的不是构建出的 wheel。"
        exit 1
        ;;
esac

# -t 也会把 console script 放到 <target>/bin，但 set_env.sh 只把
# python/site-packages 加进 PYTHONPATH，从不把该 bin 加进 PATH，
# 因此直接以模块方式调用，确保跑的是这一份。
NPUSIM="python3 -m npusim.main"
(cd "${SIM_ROOT}" && ${NPUSIM} --help | head -5)

# ==============================
# 4. 执行仿真
# ==============================
if [ "${SIM_JOBS}" -gt 1 ]; then
    log "步骤 4：仿真（并发 ${SIM_JOBS}，注意 npusim 已多线程，并发会拖慢）"
else
    log "步骤 4：仿真（串行）"
fi

# 每个样例一个子目录，避免 trace 与报告互相覆盖。
run_one_case() {
    local case_spec="$1"
    local target path bin args verify pre_cmd post_cmd
    IFS='|' read -r target path bin args verify pre_cmd post_cmd <<< "${case_spec}"

    local src_dir="${SAMPLES_BUILD_DIR}/Samples/${path}"
    local work_dir="${SIM_ROOT}/${target}"
    local run_log="${work_dir}/run.log"

    # 复制一份再跑：多个样例可能共享同一源目录，且仿真会在 CWD 写数据文件。
    mkdir -p "${work_dir}"
    cp -a "${src_dir}"/. "${work_dir}/" 2>/dev/null || true

    {
        echo "=== ${target} ==="
        echo "可执行文件：${bin}"
        echo "运行参数：${args:-<无>}"
        echo "校验方式：${verify}"
        echo "前置命令：${pre_cmd:-<无>}"
        echo "后置命令：${post_cmd:-<无>}"
    } > "${run_log}"

    # 前置命令：部分样例的输入数据需要外部先生成（cv_datapath、hif8）。
    if [ -n "${pre_cmd}" ]; then
        echo "--- 前置命令：${pre_cmd} ---" >> "${run_log}"
        if ! (cd "${work_dir}" && eval "${pre_cmd}") >> "${run_log}" 2>&1; then
            echo "  [${target}] 前置命令失败：${pre_cmd}"
            echo "PRE_FAILED" > "${work_dir}/.exit_code"
            return 0
        fi
    fi

    local -a npusim_args=(record "./${bin}" -s "${SOC_VERSION}" -o "${work_dir}/npusim_output" -g)
    if [ -n "${args}" ]; then
        npusim_args=(record "./${bin}" -s "${SOC_VERSION}" -o "${work_dir}/npusim_output" -u "${args}" -g)
    fi

    echo "--- 仿真 ---" >> "${run_log}"
    (cd "${work_dir}" && ${NPUSIM} "${npusim_args[@]}") >> "${run_log}" 2>&1
    local rc=$?
    echo "${rc}" > "${work_dir}/.exit_code"

    # 后置命令：外部比对脚本，其输出同样写入 run.log，供校验阶段 grep。
    # 即便仿真退出码非 0 也执行，方便从比对结果判断失败程度。
    if [ -n "${post_cmd}" ]; then
        echo "--- 后置命令：${post_cmd} ---" >> "${run_log}"
        (cd "${work_dir}" && eval "${post_cmd}") >> "${run_log}" 2>&1 || true
    fi

    echo "  [${target}] 仿真结束，rc=${rc}"
    return 0
}
if [ "${SIM_JOBS}" -gt 1 ]; then
    # 仅在显式要求并发时才走 xargs（不推荐，见 SIM_JOBS 处说明）。
    export -f run_one_case
    export SAMPLES_BUILD_DIR SIM_ROOT SOC_VERSION NPUSIM
    printf '%s\n' "${SAMPLE_CASES[@]}" \
        | xargs -P "${SIM_JOBS}" -I{} bash -c 'run_one_case "{}"'
else
    # 串行直接循环：日志按样例顺序输出，便于实时看进度。
    for _case in "${SAMPLE_CASES[@]}"; do
        IFS='|' read -r _t _ _ _ _ _ _ <<< "${_case}"
        echo "  [${_t}] 开始仿真..."
        run_one_case "${_case}"
    done
fi

# ==============================
# 5. 汇总校验结果
# ==============================
log "步骤 5：校验各样例结果"

PASS=0
FAIL=0
check() {
    if [ "$1" = "0" ]; then
        echo "    PASS: $2"
        PASS=$((PASS + 1))
    else
        echo "    FAIL: $2"
        FAIL=$((FAIL + 1))
    fi
}

for _case in "${SAMPLE_CASES[@]}"; do
    IFS='|' read -r _target _path _bin _args _verify _ _ <<< "${_case}"
    _work="${SIM_ROOT}/${_target}"
    _log="${_work}/run.log"
    _out="${_work}/npusim_output"

    echo "--- ${_target} ---"

    _rc=$(cat "${_work}/.exit_code" 2>/dev/null || echo "127")
    [ "${_rc}" = "0" ]
    check $? "npusim record 退出码为 0（rc=${_rc}）"

    case "${_verify}" in
        verify_pass)
            grep -q "\[PASS\] NPU results are consistent with CPU" "${_log}" 2>/dev/null
            check $? "与 CPU golden 精度一致"
            ;;
        diff_zero)
            # 这类样例即使精度不符也返回 0，只能靠日志判定。
            grep -q "Compare Difference length 0" "${_log}" 2>/dev/null
            check $? "程序内部自比对无差异"
            ;;
        test_pass)
            # 由后置的 verify_result.py 打印（README 写作 "test pass!"）。
            grep -q "test pass" "${_log}" 2>/dev/null
            check $? "verify_result.py 比对通过"
            ;;
        vec_add_pass)
            # vector_add 内部以 CPU 结果为基准校验；精度不符时样例返回 1，
            # 但仍以日志成功标记为准，避免仅依赖退出码。
            grep -q "Vector add completed successfully!" "${_log}" 2>/dev/null
            check $? "与 CPU 基准精度一致"
            ;;
        none)
            echo "    SKIP: 该样例不做数值校验"
            ;;
    esac

    # instr.bin 后续会删除，不再作为交付件判据；改为校验 cannsim_helper 的 DB 相关产物。
    [ -n "$(find "${_out}" \( -name 'chip*.db' -o -name 'cannsim_helper.log' \) -type f 2>/dev/null | head -1)" ]
    check $? "记录到 cannsim_helper DB 产物（chip*.db / cannsim_helper.log）"

    [ -n "$(find "${_out}" -type d -name '*_reports' 2>/dev/null | head -1)" ]
    check $? "生成性能报告（-g）"
done

log "smoke 汇总：${PASS} 项通过，${FAIL} 项失败"

# 把结论写入文件：流水线里这一步标了 continue-on-error，以保证产物能先上传，
# 再由后续步骤读这个文件决定 job 成败。
if [ "${FAIL}" -gt 0 ]; then
    echo "FAILED" > "${SMOKE_OUT}/result"
    echo "SMOKE TEST FAILED（产物仍会打包上传，其中含错误日志）"
    exit 1
fi

echo "PASSED" > "${SMOKE_OUT}/result"
log "SMOKE TEST PASSED"
exit 0
