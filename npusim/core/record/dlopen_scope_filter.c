/*
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * LD_PRELOAD 过滤器：把指定库 dlopen 时的 RTLD_GLOBAL 位剥掉（改为
 * RTLD_LOCAL）。
 *
 * 背景：CANN 的 libascendcl.so 会以 RTLD_GLOBAL 方式 dlopen
 * libacl_rt.so。仿真环境下 LD_LIBRARY_PATH 前置 camodel 目录，libacl_rt
 * 的依赖闭包会解析到模拟器 stub 链 （libTgWrapper.so /
 * libge_compiler.so，二者以默认可见性导出旧 ABI 的 libstdc++ weak
 * 符号）。整条闭包被提升进全局作用域后，同进程第三方新 ABI 代码的 std::regex
 * 等内联模板符号会被旧 ABI 副本插入（interposition），触发 std::bad_alloc。
 * 剥掉 RTLD_GLOBAL 后闭包留在私有作用域，与上板进程行为一致。
 *
 * 环境变量：
 *   NPUSIM_SCOPE_FILTER_LIBS
 *     目标库 basename 列表，':' 或 ',' 分隔，默认 libacl_rt.so
 *   NPUSIM_SCOPE_FILTER_DEBUG
 *     置 1 时向 stderr 打印每次剥离
 */

#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void *(*real_dlopen)(const char *, int);

static void *RealDlopen(void) {
  if (real_dlopen == NULL) {
    real_dlopen = (void *(*)(const char *, int))dlsym(RTLD_NEXT, "dlopen");
  }
  return (void *)real_dlopen;
}

/* basename 是否出现在列表中（':' 或 ',' 分隔），不修改列表缓冲区 */
static int NameInList(const char *list, const char *base) {
  size_t baseLen = strlen(base);
  const char *p = list;
  while (*p != '\0') {
    const char *start = p;
    while (*p != '\0' && *p != ':' && *p != ',') {
      p++;
    }
    size_t len = (size_t)(p - start);
    if (len == baseLen && strncmp(start, base, len) == 0) {
      return 1;
    }
    if (*p != '\0') {
      p++;
    }
  }
  return 0;
}

static int ShouldStripGlobal(const char *filename) {
  static const char *defaultTargets = "libacl_rt.so";
  if (filename == NULL) {
    return 0;
  }
  const char *slash = strrchr(filename, '/');
  const char *base = slash != NULL ? slash + 1 : filename;
  const char *targets = getenv("NPUSIM_SCOPE_FILTER_LIBS");
  if (targets == NULL || targets[0] == '\0') {
    targets = defaultTargets;
  }
  return NameInList(targets, base);
}

void *dlopen(const char *filename, int flags) {
  if (RealDlopen() == NULL) {
    return NULL;
  }
  if ((flags & RTLD_GLOBAL) != 0 && ShouldStripGlobal(filename)) {
    if (getenv("NPUSIM_SCOPE_FILTER_DEBUG") != NULL) {
      fprintf(stderr, "[scope-filter] strip RTLD_GLOBAL: %s\n", filename);
    }
    flags &= ~RTLD_GLOBAL;
  }
  return real_dlopen(filename, flags);
}
