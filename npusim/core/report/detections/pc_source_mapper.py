#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

"""PC → 源码行映射器（detection 报告源码定位用）。

独立于 BiProf DB：从 device object 文件抽出嵌入的设备 ELF（msobjdump --extract-elf），
再用 llvm-symbolizer 把 .text 段偏移解析成 源文件:行号。

为什么用 .text 偏移而不是 device PC 原值？
  npusim 日志里的 PC 是 device 运行时地址，llvm-symbolizer 只认 ELF .text 段内
  的偏移：

      text_offset = device_pc - base
      base        = min(device_pcs) & ~0xfff   # 对齐到 4K 页

降级：object_file 为空、或 msobjdump/llvm-symbolizer 不在 PATH 时，mapper 退化为 no-op，
detection 行为与现状完全一致（不引入回归）。
"""

import json
import os
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from npusim.core.public.comm import print_info, print_warn
from npusim.core.report.detections.log_patterns import DEVICE_META_PAT


@dataclass
class SourceLoc:
    """单条 PC 的源码定位。"""

    src_file: str = ""  # 源文件绝对/相对路径
    line: int = 0  # 行号
    func: str = ""  # 最内层函数名
    inlined: List[Tuple[str, str, int]] = field(default_factory=list)
    # 内联调用链（由内到外）：(func, file, line)


CACHE_VERSION = 4


# device PC 段前缀：npusim device PC 形如 0x9000_xxxx_xxxx，高 28 bit == 0x900。
# 指令日志里还有 sentinel PC（0x0 / 0x11111111 等），需排除。
_DEVICE_PC_PREFIX = 0x900
_PAGE_MASK = ~0xFFF  # 4K 页对齐


def _find_tool(name: str, *rel_paths: str) -> Optional[str]:
    """先查 PATH，再按 ASCEND_TOOLKIT_HOME / ASCEND_HOME_PATH 的相对路径找工具。

    npusim report 运行时通常已 source setenv.bash（工具在 PATH），但即便没 source，
    ASCEND_TOOLKIT_HOME 环境变量也常驻，兜底从 $ASCEND_TOOLKIT_HOME/<rel> 解析。
    """
    found = shutil.which(name)
    if found:
        return found
    ascend_home = os.environ.get("ASCEND_TOOLKIT_HOME") or os.environ.get("ASCEND_HOME_PATH")
    if ascend_home:
        for rel in rel_paths:
            cand = Path(ascend_home) / rel
            if cand.is_file():
                return str(cand)
    return None


def _cann_python_site_packages() -> Optional[str]:
    """定位 CANN 自带 python site-packages（msobjdump 模块所在）。

    msobjdump 是 `python3 -m msobjdump` 的 bash 包装，需要 CANN site-packages 在
    PYTHONPATH 上。当用户用 `PYTHONPATH=<repo> python3 -m npusim.main` 跑 report 时，
    自设 PYTHONPATH 会盖掉 setenv 注入的 CANN site-packages，导致 msobjdump 找不到模块。
    这里显式把 CANN site-packages 补回子进程环境。
    """
    ascend_home = os.environ.get("ASCEND_TOOLKIT_HOME") or os.environ.get("ASCEND_HOME_PATH")
    if not ascend_home:
        return None
    cand = Path(ascend_home) / "python" / "site-packages"
    return str(cand) if cand.is_dir() else None


def _tool_env() -> Dict[str, str]:
    """子进程环境：复制当前环境，补上 CANN site-packages 到 PYTHONPATH。"""
    env = os.environ.copy()
    cann_sp = _cann_python_site_packages()
    if cann_sp:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{cann_sp}:{existing}" if existing else cann_sp
    return env


def is_device_pc(pc: int) -> bool:
    """判定 PC 是否为 device 运行时地址（用于算 base / 决定是否符号化）。

    只用于从指令日志的 PC 里剔除 sentinel（0x0 / 0x11111111 等），
    不用于区分 GM 数据地址 —— detection 里 PC 字段取自指令日志（set_flag/wait_flag/
    load/store 的执行 PC），GM 数据地址在 addr 字段且 gm_oob 的 annotate 是 no-op，
    不会进入 is_device_pc。
    """
    return pc > 0 and (pc >> 28) == _DEVICE_PC_PREFIX


def _pc_to_offset(pc: int, base: int) -> int:
    return pc - base


class PcSourceMapper:
    """把 device PC 映射到 (源文件, 行号)。

    典型用法::

        mapper = PcSourceMapper(object_file)
        mapper.build({"kernel0": [0x9000d0d234, ...]})   # 各 kernel 的 device PC
        loc = mapper.lookup("kernel0", 0x9000d0d234)     # → SourceLoc(...)
    """

    def __init__(self, object_file: Optional[str]) -> None:
        self.object_file = object_file
        self._msobjdump = _find_tool("msobjdump", "tools/msobjdump/msobjdump")
        self._symbolizer = _find_tool(
            "llvm-symbolizer", "tools/msopprof/bin/llvm-symbolizer", "tools/mssanitizer/bin/llvm-symbolizer"
        )
        # (kernel_id, pc) → Optional[SourceLoc]。None 也必须保留：
        # 它表示该 PC 已处理但没有可展示的用户源码行，pc_source_map 仍要包含 PC。
        self._cache: Dict[Tuple[str, int], Optional[SourceLoc]] = {}
        # (kernel_id, elf_offset) → Optional[SourceLoc]。offset 与 runtime base
        # 解耦，同一 object 换 load base 后仍可复用缓存。
        self._offset_cache: Dict[Tuple[str, int], Optional[SourceLoc]] = {}
        # kernel_id → base（device PC 减去的基址）
        self._bases: Dict[str, int] = {}
        # kernel_id → 抽出的设备 ELF 路径
        self._device_objs: Dict[str, Path] = {}
        # 抽出的设备 ELF 所在临时目录；持有该对象以保证映射器生命周期内可用。
        self._extract_root: Optional[tempfile.TemporaryDirectory] = None
        # 是否已从持久化缓存装载（用于避免重复 save 相同内容）
        self._loaded_from_cache = False
        self._available = bool(object_file) and bool(self._msobjdump) and bool(self._symbolizer)
        if object_file and not self._available:
            missing = []
            if not self._msobjdump:
                missing.append("msobjdump")
            if not self._symbolizer:
                missing.append("llvm-symbolizer")
            if missing:
                print_warn(
                    f"Source mapping disabled: tool(s) {', '.join(missing)} not found "
                    f"(detection will run without source-line info)."
                )

    @property
    def available(self) -> bool:
        return self._available or bool(self._cache)

    @property
    def loaded_from_cache(self) -> bool:
        return self._loaded_from_cache

    # ------------------------------------------------------------------
    # 构建 / 查询
    # ------------------------------------------------------------------
    def build(self, device_pcs_by_kernel: Dict[str, List[int]]) -> None:
        """预计算各 kernel 的基址并抽出设备 ELF。符号化延迟到 lookup_many。"""
        if self._available and not self._device_objs:
            extracted = self._extract_device_objs()
            if not extracted:
                self._available = False
            else:
                self._device_objs = extracted
                names = ", ".join(self._device_objs.keys())
                print_info(f"Source mapping: extracted device ELF(s): {names}")
        if not self._available and not self._offset_cache:
            return
        if not self._device_objs and self._available:
            return
        for kernel_id, pcs in device_pcs_by_kernel.items():
            dev_pcs = [p for p in pcs if is_device_pc(p)]
            if not dev_pcs:
                continue
            base = self._infer_base_from_offsets(kernel_id, dev_pcs)
            if base is None and kernel_id not in self._bases:
                base = min(dev_pcs) & _PAGE_MASK
            if base is not None:
                self._bases[kernel_id] = base

    def prebuild(self, device_pcs_by_kernel: Dict[str, List[int]]) -> None:
        """提前符号化所有 device PC（生成持久化缓存前调用）。"""
        self.build(device_pcs_by_kernel)
        for kernel_id, pcs in device_pcs_by_kernel.items():
            self.lookup_many(kernel_id, pcs)

    def _resolve_device_obj(self, kernel_id: str) -> Optional[Path]:
        """按 kernel_id 查找设备 ELF；多 kernel 时按名称匹配。"""
        if not self._device_objs:
            return None
        if len(self._device_objs) == 1:
            return next(iter(self._device_objs.values()))
        for name, obj in self._device_objs.items():
            if name in kernel_id or kernel_id in name:
                return obj
        return next(iter(self._device_objs.values()))

    def _base_for(self, kernel_id: str, pcs: List[int]) -> int:
        dev_pcs = [p for p in pcs if is_device_pc(p)]
        inferred = self._infer_base_from_offsets(kernel_id, dev_pcs)
        if inferred is not None:
            self._bases[kernel_id] = inferred
            return inferred
        if kernel_id in self._bases:
            return self._bases[kernel_id]
        base = (min(dev_pcs) & _PAGE_MASK) if dev_pcs else 0
        self._bases[kernel_id] = base
        return base

    def _infer_base_from_offsets(self, kernel_id: str, dev_pcs: List[int]) -> Optional[int]:
        """用已缓存的 ELF offset 集合反推当前 runtime base。

        runtime base 是页对齐的，因此 ``pc`` 和 ``offset`` 的低 12 bit 必须相同。
        对每个可能 candidate 统计能命中的 PC 数，取命中数最多者；同一份 ELF
        换 load base 后，这个匹配能稳定恢复新 base。
        """
        offsets = {off for kid, off in self._offset_cache if kid == kernel_id}
        if not offsets or not dev_pcs:
            return None
        pcs = list(dict.fromkeys(dev_pcs))
        pc_set = set(pcs)
        offsets_by_low: Dict[int, set] = defaultdict(set)
        for off in offsets:
            offsets_by_low[off & 0xFFF].add(off)

        candidates = set()
        for pc in pcs:
            for off in offsets_by_low[pc & 0xFFF]:
                candidate = pc - off
                if candidate >= 0 and candidate & 0xFFF == 0:
                    candidates.add(candidate)
        if not candidates:
            return None

        def hits(candidate: int) -> int:
            return sum(1 for pc in pc_set if pc - candidate in offsets)

        max_hits = max(hits(candidate) for candidate in candidates)
        best = [candidate for candidate in candidates if hits(candidate) == max_hits]
        if len(best) > 1:
            print_warn(
                f"Source mapping: ambiguous runtime base candidates for {kernel_id}; "
                f"using min({', '.join(f'0x{x:x}' for x in best[:4])}{', ...' if len(best) > 4 else ''})"
            )
        return min(best)

    def lookup_many(self, kernel_id: str, pcs: List[int]) -> Dict[int, Optional[SourceLoc]]:
        """批量查一组 PC 的源码定位。首次调用某 kernel_id 时触发批量符号化并缓存。"""
        if not self._available and not self._offset_cache:
            return {pc: None for pc in pcs}

        base = self._base_for(kernel_id, pcs)
        device_obj = self._resolve_device_obj(kernel_id) if self._available else None
        # 分出未缓存的 device PC
        to_resolve: List[int] = []
        offset_of: Dict[int, int] = {}
        for pc in pcs:
            if (kernel_id, pc) in self._cache:
                continue
            if not is_device_pc(pc):
                self._cache[(kernel_id, pc)] = None
                continue
            off = _pc_to_offset(pc, base)
            cached_loc = self._offset_cache.get((kernel_id, off))
            if (kernel_id, off) in self._offset_cache:
                self._cache[(kernel_id, pc)] = cached_loc
                continue
            offset_of[pc] = off
            to_resolve.append(off)

        if to_resolve:
            mapped = self._symbolize_offsets(device_obj, to_resolve)
            for pc, off in offset_of.items():
                self._cache[(kernel_id, pc)] = mapped.get(off)
                self._offset_cache[(kernel_id, off)] = mapped.get(off)

        return {pc: self._cache.get((kernel_id, pc)) for pc in pcs}

    def lookup(self, kernel_id: str, pc: int) -> Optional[SourceLoc]:
        return self.lookup_many(kernel_id, [pc]).get(pc)

    # ------------------------------------------------------------------
    # 持久化缓存
    # ------------------------------------------------------------------
    def save(self, cache_path: Path) -> None:
        """把已解析的 PC→源码行映射写成 JSON 缓存。

        缓存内部以 ELF 偏移为 key（pc - base），同一份 object 的 runtime base 变化
        时缓存仍可复用；同时保存 runtime pc，便于后续直接查询。
        """
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        kernels: Dict[str, dict] = {}
        for (kernel_id, offset), loc in self._offset_cache.items():
            base = self._bases.get(kernel_id, 0)
            if base <= 0:
                continue
            kernel = kernels.setdefault(
                kernel_id,
                {
                    "base": f"0x{base:x}",
                    "entries": {},
                },
            )
            kernel["entries"][f"0x{offset:x}"] = {
                "pc": f"0x{base + offset:x}",
                **(
                    {
                        "src_file": loc.src_file,
                        "line": loc.line,
                        "func": loc.func,
                        "inlined": [{"func": f, "src_file": fl, "line": ln} for f, fl, ln in loc.inlined],
                    }
                    if loc is not None
                    else {}
                ),
            }

        data = {
            "version": CACHE_VERSION,
            "object_file": str(Path(self.object_file).resolve()) if self.object_file else "",
            "object_meta": self._object_meta(),
            "kernels": kernels,
        }
        cache_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _object_meta(self) -> Dict[str, int]:
        """读取 object 文件一致性元数据；无对象或读取失败时返回空。"""
        if not self.object_file:
            return {}
        try:
            stat = Path(self.object_file).stat()
        except OSError:
            return {}
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

    @classmethod
    def load(cls, cache_path: Path, object_file: Optional[str] = None) -> "PcSourceMapper":
        """从 JSON 缓存装载映射；cache 覆盖查询时无需 msobjdump/symbolizer。"""
        cache_path = Path(cache_path)
        if not cache_path.is_file():
            return cls(object_file)
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print_warn(f"Source mapping cache load failed: {e}")
            return cls(object_file)
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            print_warn("Source mapping cache ignored: unsupported format")
            return cls(object_file)

        cached_object = cls._cache_object_path(data)
        if not cls._object_file_matches(object_file, cached_object):
            return cls(object_file)
        chosen_object = cls._choose_object_file(object_file, cached_object)
        if not cls._cached_object_is_valid(chosen_object, data):
            return cls(object_file)
        mapper = cls(chosen_object)
        for kernel_id, kernel in data.get("kernels", {}).items():
            base = cls._load_kernel_base(mapper, kernel_id, kernel)
            if base <= 0:
                continue
            for offset_str, entry in kernel.get("entries", {}).items():
                cls._load_cache_entry(mapper, kernel_id, base, offset_str, entry)
        if mapper._offset_cache:
            mapper._loaded_from_cache = True
        return mapper

    @staticmethod
    def _cache_object_path(data: dict) -> Optional[Path]:
        """解析缓存中记录的 object 文件路径；无效路径返回 None。"""
        cached_object = data.get("object_file", "")
        return Path(cached_object).resolve() if cached_object else None

    @staticmethod
    def _object_file_matches(explicit_object: Optional[str], cached_object_path: Optional[Path]) -> bool:
        """显式 object 与缓存 object 不一致时，旧缓存不可信。"""
        if not explicit_object or not cached_object_path:
            return True
        return Path(explicit_object).resolve() == cached_object_path

    @staticmethod
    def _choose_object_file(explicit_object: Optional[str], cached_object_path: Optional[Path]) -> Optional[str]:
        """优先使用调用者指定的 object；未指定时回退缓存中的有效 object。"""
        if explicit_object is not None:
            return explicit_object
        if cached_object_path and cached_object_path.is_file():
            return str(cached_object_path)
        return None

    @classmethod
    def _cached_object_is_valid(cls, chosen_object: Optional[str], data: dict) -> bool:
        """校验缓存 object 的一致性元数据；object 变化时禁止复用旧映射。"""
        if not chosen_object:
            return True
        cached_meta = data.get("object_meta") if isinstance(data.get("object_meta"), dict) else {}
        if cls._object_meta_matches(chosen_object, cached_meta):
            return True
        print_warn("Source mapping cache ignored: object file changed")
        return False

    @staticmethod
    def _object_meta_matches(path: str, cached_meta: dict) -> bool:
        """校验 object 文件 size/mtime 是否与缓存一致；元数据缺失视为不一致。"""
        if not cached_meta or "size" not in cached_meta or "mtime_ns" not in cached_meta:
            return False
        try:
            stat = Path(path).stat()
            return stat.st_size == int(cached_meta["size"]) and stat.st_mtime_ns == int(cached_meta["mtime_ns"])
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _load_kernel_base(mapper: "PcSourceMapper", kernel_id: str, kernel: dict) -> int:
        """读取并记录一个 kernel 的缓存基址；无效基址返回 0。"""
        try:
            base = int(kernel.get("base", "0"), 0)
        except (TypeError, ValueError):
            return 0
        if base <= 0:
            return 0
        mapper._bases[kernel_id] = base
        return base

    @staticmethod
    def _parse_inlined(entry: dict) -> List[Tuple[str, str, int]]:
        """解析缓存中的 inline 调用链。"""
        return [
            (item.get("func", ""), item.get("src_file", ""), int(item.get("line", 0)))
            for item in entry.get("inlined", [])
            if isinstance(item, dict)
        ]

    @staticmethod
    def _cache_source_loc(entry: dict, inlined: List[Tuple[str, str, int]]) -> Optional[SourceLoc]:
        """从主定位或 inline 链中还原可展示的 SourceLoc。

        兼容旧缓存/测试数据：没有 inline 链时，主定位只有在本身就是源码文件时
        才允许展示；CANN 头文件一律禁止回退。
        """
        user_frame = _first_user_frame(inlined)
        if user_frame is None:
            main_file = entry.get("src_file", "")
            if _is_user_source(main_file):
                user_frame = (entry.get("func", ""), main_file, int(entry.get("line", 0)))
        if user_frame is None:
            return None
        return SourceLoc(src_file=user_frame[1], line=user_frame[2], func=user_frame[0], inlined=inlined)

    @classmethod
    def _load_cache_entry(
        cls,
        mapper: "PcSourceMapper",
        kernel_id: str,
        base: int,
        offset_str: str,
        entry: dict,
    ) -> None:
        """将一条缓存映射还原到 runtime PC 与 ELF offset 双索引。"""
        try:
            offset = int(offset_str, 0)
            pc = int(entry.get("pc", "0"), 0)
        except (TypeError, ValueError):
            return
        if pc <= 0:
            return
        loc = cls._cache_source_loc(entry, cls._parse_inlined(entry))
        mapper._cache[(kernel_id, pc)] = loc
        mapper._offset_cache[(kernel_id, offset)] = loc
        # offset key 与 runtime pc 双索引，load 后 base 相同即可直接命中
        mapper._cache.setdefault((kernel_id, base + offset), loc)

    # ------------------------------------------------------------------
    # 抽取嵌入的设备 ELF
    # ------------------------------------------------------------------
    def _extract_device_objs(self) -> Dict[str, Path]:
        """msobjdump --extract-elf 抽出所有设备对象，返回 {kernel_name: path}。

        回退：object_file 本身就是单算子设备 ELF 时，直接用它符号化，kernel 名
        读自其元数据。读不到 kernel 名时返回空映射。
        """
        self._extract_root = tempfile.TemporaryDirectory(prefix="cannsym_srcmap_")
        out_dir = Path(self._extract_root.name)
        try:
            res = subprocess.run(
                [self._msobjdump, "--extract-elf", str(self.object_file), "--out-dir", str(out_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                env=_tool_env(),
            )
        except Exception as e:
            print_warn(f"msobjdump extract-elf failed: {e}")
            return {}
        if res.returncode != 0:
            print_warn(f"msobjdump extract-elf failed (rc={res.returncode}): {res.stderr.strip()[:200]}")
        else:
            device_objs = sorted(out_dir.glob("*.aicore.o"))
            if device_objs:
                result: Dict[str, Path] = {}
                for obj in device_objs:
                    kernel_name = self._read_kernel_name(obj) or obj.stem
                    result[kernel_name] = obj
                return result

        # 回退：单算子设备 ELF 直接符号化。
        kernel_name = self._read_kernel_name(self.object_file)
        if kernel_name:
            return {kernel_name: Path(self.object_file)}
        return {}

    def close(self) -> None:
        """Release temporary device ELF files extracted by msobjdump."""
        self._device_objs.clear()
        if self._extract_root is not None:
            self._extract_root.cleanup()
            self._extract_root = None

    def __enter__(self) -> "PcSourceMapper":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def _read_kernel_name(self, device_obj: Path) -> Optional[str]:
        """从设备对象元数据读取 kernel 名。"""
        try:
            res = subprocess.run(
                [self._msobjdump, "--dump-elf", str(device_obj)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                env=_tool_env(),
            )
        except Exception:
            return None
        if res.returncode != 0:
            return None
        for line in res.stdout.splitlines():
            m = DEVICE_META_PAT.match(line.strip())
            if m:
                return m.group(1)
        return None

    # ------------------------------------------------------------------
    # 批量符号化
    # ------------------------------------------------------------------
    def _symbolize_offsets(self, device_obj: Path, offsets: List[int]) -> Dict[int, Optional[SourceLoc]]:
        """llvm-symbolizer 批量解析。stdin 每行一个 0x 偏移。

        每个 PC 的输出可能含**多个空行分隔的块**：先内联帧块（func/loc 成对），
        再可能跟一个孤立的 loc 块（行表记录、无函数名）。后者属于同一个 PC。
        因此按块切分后，把「孤立 loc 块」（首行即位置、无函数名）合并到前一个 PC。
        """
        if not offsets:
            return {}
        stdin = "\n".join(f"0x{off:x}" for off in offsets) + "\n"
        try:
            res = subprocess.run(
                [self._symbolizer, f"--obj={device_obj}"],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except Exception as e:
            print_warn(f"llvm-symbolizer failed: {e}")
            return {off: None for off in offsets}

        # 按空行切块，过滤空块
        blocks = [b for b in res.stdout.split("\n\n") if b.strip()]
        # 把孤立 loc 块合并到前一个块（同一 PC）
        records: List[str] = []
        for blk in blocks:
            first_nonblank = next((ln for ln in blk.splitlines() if ln.strip()), "")
            if records and _looks_like_loc(first_nonblank.strip()):
                # 孤立 loc 块 → 并入前一个 PC 的输出
                records[-1] = records[-1] + "\n" + blk
            else:
                records.append(blk)

        # 存在无调试信息的偏移时，llvm-symbolizer 对它们输出空块，上面的
        # split(..., if b.strip()) 会把空块过滤掉，导致 len(records) != len(offsets)，
        # zip 对位错乱 → 后面的偏移解析错误/None。检测到数量不一致就退回逐条符号化
        # （单条查询无语义歧义，可保证正确）。
        if len(records) != len(offsets):
            result: Dict[int, Optional[SourceLoc]] = {}
            for off in offsets:
                result[off] = self._symbolize_one(device_obj, off)
            return result

        result: Dict[int, Optional[SourceLoc]] = {}
        for off, rec in zip(offsets, records):
            result[off] = self._parse_frame(rec)
        for off in offsets:
            result.setdefault(off, None)
        return result

    def _symbolize_one(self, device_obj: Path, off: int) -> Optional[SourceLoc]:
        """对单个偏移 LLVM 符号化并解析（无语义歧义，用于批量对位失败时回退）。"""
        stdin = f"0x{off:x}\n"
        try:
            res = subprocess.run(
                [self._symbolizer, f"--obj={device_obj}"],
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except Exception as e:
            print_warn(f"llvm-symbolizer failed: {e}")
            return None
        return self._parse_frame(res.stdout)

    @staticmethod
    def _parse_frame(frame: str) -> Optional[SourceLoc]:
        """解析 llvm-symbolizer 单个地址的输出块。

        标准格式为 func/file 成对的内联帧（**由内到外**：最内层=违规指令实际所在的源行，
        最外层=调用点）。PC 命中无 DIE 却有行表记录时，末尾还可能出现**孤立的
        `file:line:col` 行**（无前导函数名），那是更外层的调用点。

        选帧策略：取**最内层的用户源帧**——它最贴近违规指令本身（如 SetFlag 调用行），
        而非被 inline 进的外层调用点（如 op.Process() 调用处）。无用户帧时取最内层帧。
        """
        lines = [ln for ln in frame.splitlines() if ln.strip()]
        if not lines:
            return None
        inlined: List[Tuple[str, str, int]] = []
        func = ""
        i = 0
        while i < len(lines):
            cur = lines[i].strip()
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if nxt and _looks_like_loc(nxt):
                # 成对：func / loc
                file_i, line_i = _split_file_line(nxt)
                if not inlined:
                    func = cur
                inlined.append((cur, file_i, line_i))
                i += 2
            elif _looks_like_loc(cur):
                # 孤立 loc 行（无前导函数名，更外层调用点）
                file_i, line_i = _split_file_line(cur)
                inlined.append(("", file_i, line_i))
                i += 1
            else:
                # 纯函数行无 loc，跳过
                i += 1
        if not inlined:
            return None
        # 展示策略：只显示源码文件中的行，禁止显示 CANN/lib 头文件。
        # PC 仍然由调用方保留；若 inline 链里没有源码文件帧，则该 PC 没有
        # 可展示的源码行（不能退回 lib 头文件，避免报告把用户误导到框架内部）。
        user_frame = _first_user_frame(inlined)
        if user_frame is None:
            return None
        best_func, best_file, best_line = user_frame
        if not best_func:
            best_func = func
        return SourceLoc(src_file=best_file, line=best_line, func=best_func, inlined=inlined)


def _split_file_line(loc: str) -> Tuple[str, int]:
    """拆 `path:line:col` → (path, line)。路径可能含冒号（Windows 盘符 / 含冒号的目录），
    所以从右拆两段。line/col 解析失败时 line=0。"""
    parts = loc.rsplit(":", 2)
    if len(parts) == 3:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return loc, 0
    if len(parts) == 2:
        try:
            return parts[0], int(parts[1])
        except ValueError:
            return loc, 0
    return loc, 0


def _looks_like_loc(s: str) -> bool:
    """判定一行是否为位置记录 `file:line:col` 或 `file:line`（末 1~2 段是数字）。

    注意：纯函数名（如 ``DataMover::Process()``）不含冒号或末段非数字，
    不应误判为位置记录。
    """
    parts = s.rsplit(":", 2)
    if len(parts) < 2:
        return False
    # 末段必须是数字（line 或 col）
    try:
        int(parts[-1])
    except ValueError:
        return False
    # file:line:col → 倒数第二段也是数字（line）；file:line → 倒数第二段是文件路径
    if len(parts) == 3:
        try:
            int(parts[-2])
        except ValueError:
            return False
    return True


def _is_user_source(path: str) -> bool:
    """判定是否为用户源码文件（允许用户 .h，禁止 CANN/lib 头文件）。

    用户工程本身也可能包含 .h/.hpp，因此不能按后缀一刀切排除头文件；
    这里按路径/文件特征排除 CANN 与系统库头，用户工程内的头文件仍可展示。
    未知后缀不作为源码展示，避免把框架内部文件或其他中间产物带入报告。
    """
    if not path:
        return False
    low = path.lower()
    if "/asc/include/" in low or "/impl/basic_api/" in low:
        return False
    if "kernel_operator" in low or "kernel_event" in low or "kernel_utils" in low:
        return False
    if low.startswith(("/usr/include/", "/usr/local/include/", "/opt/")) or "/usr/lib/" in low:
        return False
    return low.endswith((".asc", ".cpp", ".c", ".cc", ".cu", ".cxx", ".h", ".hpp"))


def _first_user_frame(
    inlined: List[Tuple[str, str, int]],
) -> Optional[Tuple[str, str, int]]:
    """返回 inline 链中最内层的用户源码帧；无用户帧时返回 None。"""
    for frame in inlined:
        if _is_user_source(frame[1]):
            return frame
    return None


# ----------------------------------------------------------------------
# 公共 API
# ----------------------------------------------------------------------
def build_source_mapper(object_file: Optional[str]) -> PcSourceMapper:
    """工厂：object_file 为空或工具缺失时返回 no-op mapper。"""
    return PcSourceMapper(object_file)


def load_source_mapper(cache_path: Path, object_file: Optional[str] = None) -> PcSourceMapper:
    """优先加载持久化映射缓存；cache 不可用时退回即时构建。"""
    mapper = PcSourceMapper.load(Path(cache_path), object_file)
    if mapper._loaded_from_cache:
        return mapper
    if object_file is None and mapper.available:
        # 无显式 object 时，已通过缓存中的路径完成一致性校验并成功装载；
        # 工具缺失不应把这份可用缓存降级成 no-op。
        return mapper
    return PcSourceMapper(object_file)
