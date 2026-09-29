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

"""Unit tests for npusim.core.report.detections.pc_source_mapper.

Covers the device-PC → source-file:line mapping added for source-level
detection reports. The mapping needs msobjdump + llvm-symbolizer (CANN toolkit);
when those are absent the mapper degrades to a no-op (detection unaffected).
The end-to-end path (real device ELF) is exercised only when the CANN toolkit
environment is available; otherwise the structural/edge-case tests still run.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from npusim.core.report.detections.pc_source_mapper import (
    PcSourceMapper,
    SourceLoc,
    _looks_like_loc,
    _split_file_line,
    build_source_mapper,
    is_device_pc,
)

from npusim.core.report.detections.instr_log_parser import (
    collect_device_pcs_from_record_db,
)

from npusim.core.report.detections import pc_source_mapper


def _fake_symbolize_offsets_with_inline(obj, offsets):
    del obj
    return {
        0x16C: SourceLoc(
            src_file="/ws/memcopy.asc",
            line=35,
            func="MemcopyOperator::Process",
            inlined=[
                ("CopyGmToUbufAlignV2", "/asc/include/copy.h", 92),
                ("MemcopyOperator::Process", "/ws/memcopy.asc", 35),
            ],
        )
        for _offset in offsets
    }


def _fake_symbolize_offsets_partial(obj, offsets):
    del obj
    return {
        0x16C: SourceLoc(src_file="/ws/memcopy.asc", line=35, func="f"),
        0x170: None,
    }


def _fake_symbolize_offsets(obj, offsets):
    del obj
    return {0x16C: SourceLoc(src_file="/ws/memcopy.asc", line=35, func="f") for _offset in offsets}


def _write_object(path: Path) -> None:
    path.write_bytes(b"device-object")


def _assert_no_symbolize(*_args):
    raise AssertionError("symbolizer should not be called")


class PcSourceMapperHelpersTest(unittest.TestCase):
    def test_is_device_pc(self):
        self.assertTrue(is_device_pc(0x9000D0D234))
        self.assertTrue(is_device_pc(0x9000D0D000))
        self.assertFalse(is_device_pc(0x0))
        self.assertFalse(is_device_pc(0x11111111))
        self.assertFalse(is_device_pc(0x1))

    def test_split_file_line(self):
        self.assertEqual(_split_file_line("/a/b/c.asc:39:8"), ("/a/b/c.asc", 39))
        self.assertEqual(_split_file_line("/a/b/c.asc:39"), ("/a/b/c.asc", 39))
        self.assertEqual(_split_file_line("no:colons:here"), ("no:colons:here", 0))

    def test_looks_like_loc(self):
        self.assertTrue(_looks_like_loc("/x/y.asc:39:8"))
        self.assertTrue(_looks_like_loc("/x/y.asc:39"))
        self.assertFalse(_looks_like_loc("DataMover::Process() (.vector)"))
        self.assertFalse(_looks_like_loc("bare"))

    def test_parse_frame_inline_chain_picks_user_frame(self):
        raw = (
            "void AscendC::SetFlagInternal(event_t) (.vector)\n"
            "/asc/include/kernel_event.h:305:13\n"
            "DataMover::Process() (.vector)\n"
            "/ws/flag_oob.asc:22:9\n"
        )
        loc = PcSourceMapper._parse_frame(raw)
        self.assertIsInstance(loc, SourceLoc)
        self.assertEqual(loc.line, 22)
        self.assertTrue(loc.src_file.endswith("flag_oob.asc"))

    def test_parse_frame_orphan_loc_block_picks_innermost_user_frame(self):
        """从内联链中取最内层用户帧。"""
        raw = "DataMover::Process() (.vector)\n/ws/flag_oob.asc:22:9\n\n/ws/flag_oob.asc:39:8\n"
        loc = PcSourceMapper._parse_frame(raw)
        self.assertEqual(loc.line, 22)
        self.assertTrue(loc.src_file.endswith("flag_oob.asc"))

    def test_parse_frame_empty(self):
        self.assertIsNone(PcSourceMapper._parse_frame(""))
        self.assertIsNone(PcSourceMapper._parse_frame("\n\n"))

    def test_parse_frame_drops_pure_lib_frames(self):
        """禁止展示 CANN/lib 头文件；纯 lib inline 链不产生源码定位。"""
        raw = (
            "AscendC::AscendCUtils::InitSocStateImpl() (.vector)\n"
            "/asc/impl/basic_api/kernel_utils_base.h:78:1\n"
            "AscendC::InitSocState() (.vector)\n"
            "/asc/impl/basic_api/kernel_operator_common_intf_impl.h:121:1\n"
        )
        self.assertIsNone(PcSourceMapper._parse_frame(raw))

    def test_parse_frame_prefers_first_user_frame(self):
        raw = (
            "AscendC::CopyGmToUbufAlignV2() (.vector)\n"
            "/asc/impl/basic_api/kernel_operator_data_copy_impl.h:92:1\n"
            "MemcopyOperator::Process() (.vector)\n"
            "/ws/memcopy.asc:35:9\n"
        )
        loc = PcSourceMapper._parse_frame(raw)
        self.assertEqual((loc.src_file, loc.line), ("/ws/memcopy.asc", 35))
        self.assertEqual(loc.func, "MemcopyOperator::Process() (.vector)")


class PcSourceMapperDegradeTest(unittest.TestCase):
    def test_no_object_file_is_noop(self):
        m = build_source_mapper(None)
        self.assertFalse(m.available)
        self.assertIsNone(m.lookup("mix_mode", 0x9000D0D234))

    def test_prebuild_save_and_load_cache(self):
        """prebuild → save → load 后 cache 命中，不再调用 llvm-symbolizer。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "pc_source_map.json"
            with patch.object(pc_source_mapper, "_find_tool", return_value="/fake/tool"):
                m = PcSourceMapper("/some/object.o")
            m._available = True
            m._device_objs = {"kernel": Path("/fake/device.o")}
            m._bases = {"kernel": 0x9000D0D000}
            m._symbolize_offsets = _fake_symbolize_offsets_with_inline
            m.prebuild({"kernel": [0x9000D0D16C]})
            m.save(cache)

            loaded = PcSourceMapper.load(cache)
            self.assertTrue(loaded.available)
            self.assertTrue(loaded.loaded_from_cache)
            self.assertIsNone(loaded.object_file)
            loc = loaded.lookup("kernel", 0x9000D0D16C)
            self.assertIsNotNone(loc)
            self.assertEqual(loc.src_file, "/ws/memcopy.asc")
            self.assertEqual(loc.line, 35)
            self.assertEqual(loc.inlined[0][0], "CopyGmToUbufAlignV2")

            loaded._symbolize_offsets = _assert_no_symbolize
            self.assertEqual(loaded.lookup("kernel", 0x9000D0D16C).line, 35)

    def test_cache_keeps_pc_without_displayable_source(self):
        """无用户源码帧的 PC 也必须保留在 pc_source_map，不能因为过滤 lib 行丢 PC。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "pc_source_map.json"
            with patch.object(pc_source_mapper, "_find_tool", return_value="/fake/tool"):
                m = PcSourceMapper("/some/object.o")
            m._available = True
            m._device_objs = {"kernel": Path("/fake/device.o")}
            m._bases = {"kernel": 0x9000D0D000}
            m._symbolize_offsets = _fake_symbolize_offsets_partial
            m.prebuild({"kernel": [0x9000D0D16C, 0x9000D0D170]})
            m.save(cache)

            data = json.loads(cache.read_text(encoding="utf-8"))
            entries = data["kernels"]["kernel"]["entries"]
            self.assertIn("0x16c", entries)
            self.assertIn("0x170", entries)
            self.assertEqual(entries["0x170"]["pc"], "0x9000d0d170")
            self.assertNotIn("src_file", entries["0x170"])

    def test_load_reprojects_cached_lib_lines_to_user_frame(self):
        """旧缓存主行可能在 CANN 头里；装载时按 inline 链重选第一个用户帧。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "pc_source_map.json"
            cache.write_text(
                json.dumps(
                    {
                        "version": pc_source_mapper.CACHE_VERSION,
                        "object_file": "",
                        "kernels": {
                            "kernel": {
                                "base": "0x9000d0d000",
                                "entries": {
                                    "0x16c": {
                                        "pc": "0x9000d0d16c",
                                        "src_file": "/asc/impl/basic_api/kernel_operator_data_copy_impl.h",
                                        "line": 92,
                                        "func": "AscendC::CopyGmToUbufAlignV2",
                                        "inlined": [
                                            {
                                                "func": "AscendC::CopyGmToUbufAlignV2",
                                                "src_file": "/asc/impl/basic_api/kernel_operator_data_copy_impl.h",
                                                "line": 92,
                                            },
                                            {
                                                "func": "MemcopyOperator::Process",
                                                "src_file": "/ws/memcopy.asc",
                                                "line": 35,
                                            },
                                        ],
                                    }
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            loaded = PcSourceMapper.load(cache)
            loc = loaded.lookup("kernel", 0x9000D0D16C)
            self.assertEqual((loc.src_file, loc.line), ("/ws/memcopy.asc", 35))

    def test_collect_device_pcs_from_record_db(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            root = Path(tmp)
            db_path = root / "chip0.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE ExecutedInstructions ("
                    "ExecInstrId INTEGER PRIMARY KEY, "
                    "ExecInstrCoreId INTEGER, "
                    "CoreTypeId INTEGER, "
                    "SourceInstrAddr INTEGER)"
                )
                conn.executemany(
                    "INSERT INTO ExecutedInstructions VALUES (?, ?, ?, ?)",
                    [
                        (1, 0, 2, 0x9000D0D16C),
                        (2, 0, 2, 0x9000D0D16C),
                        (3, 0, 3, 0x9000D0D184),
                        (4, 0, 1, 0x9000D0D1A0),
                        (5, 0, 2, 0),
                    ],
                )

            pcs = collect_device_pcs_from_record_db(root)
            self.assertEqual(
                pcs,
                {
                    "core0.vector0": [0x9000D0D16C],
                    "core0.vector1": [0x9000D0D184],
                    "core0.cube0": [0x9000D0D1A0],
                },
            )

    def test_cache_reuses_offsets_when_runtime_base_changes(self):
        """缓存以 ELF offset 为主键；同一 object 换 runtime base 后仍可命中。"""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "pc_source_map.json"
            obj = Path(tmp) / "device.asc.o"
            _write_object(obj)
            with patch.object(pc_source_mapper, "_find_tool", return_value="/fake/tool"):
                m = PcSourceMapper(str(obj))
            m._available = True
            m._device_objs = {"kernel": Path("/fake/device.o")}
            m._bases = {"kernel": 0x9000D0D000}
            m._symbolize_offsets = _fake_symbolize_offsets
            m.prebuild({"kernel": [0x9000D0D16C]})
            m.save(cache)

            loaded = PcSourceMapper.load(cache, str(obj))
            loc = loaded.lookup("kernel", 0x9000E0E16C)
            self.assertIsNotNone(loc)
            self.assertEqual(loc.line, 35)

    def test_missing_tools_is_noop(self):
        with patch.object(pc_source_mapper, "_find_tool", return_value=None):
            m = PcSourceMapper("/some/object.o")
        self.assertFalse(m.available)
        self.assertIsNone(m.lookup("mix_mode", 0x9000D0D234))

    def test_cache_invalidated_when_object_changes(self):
        """object size/mtime 变化后旧缓存必须重建，不得复用旧映射。"""
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "device.asc.o"
            _write_object(obj)
            cache = Path(tmp) / "pc_source_map.json"
            with patch.object(pc_source_mapper, "_find_tool", return_value="/fake/tool"):
                m = PcSourceMapper(str(obj))
            m._available = True
            m._device_objs = {"kernel": Path("/fake/device.o")}
            m._bases = {"kernel": 0x9000D0D000}
            m._symbolize_offsets = _fake_symbolize_offsets
            m.prebuild({"kernel": [0x9000D0D16C]})
            m.save(cache)

            obj.write_bytes(b"device-object-rebuilt")

            loaded = PcSourceMapper.load(cache, str(obj))
            self.assertFalse(loaded.loaded_from_cache)
            self.assertEqual(loaded.object_file, str(obj))

    def test_cached_mapper_is_available_without_tools(self):
        """无 CANN 工具但存在一致缓存时，缓存仍可直接提供映射。"""
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "device.asc.o"
            _write_object(obj)
            cache = Path(tmp) / "pc_source_map.json"
            with patch.object(pc_source_mapper, "_find_tool", return_value="/fake/tool"):
                m = PcSourceMapper(str(obj))
            m._available = True
            m._device_objs = {"kernel": Path("/fake/device.o")}
            m._bases = {"kernel": 0x9000D0D000}
            m._symbolize_offsets = _fake_symbolize_offsets
            m.prebuild({"kernel": [0x9000D0D16C]})
            m.save(cache)

            with patch.object(pc_source_mapper, "_find_tool", return_value=None):
                loaded = pc_source_mapper.load_source_mapper(cache, str(obj))

            self.assertTrue(loaded.available)
            self.assertTrue(loaded.loaded_from_cache)
            self.assertEqual(loaded.lookup("kernel", 0x9000D0D16C).line, 35)


def _cann_toolkit_available():
    return bool(PcSourceMapper("/nonexistent.o")._msobjdump) and bool(PcSourceMapper("/nonexistent.o")._symbolizer)


@unittest.skipUnless(_cann_toolkit_available(), "msobjdump + llvm-symbolizer not available (CANN toolkit not on PATH)")
class PcSourceMapperIntegrationTest(unittest.TestCase):
    """端到端：用 flag_oob 的 device ELF 断言 0x234 偏移解析到 flag_oob.asc:22。

    需要环境里已编出 examples/synccheck/flag_oob/build/flag_oob_san.asc.o。
    该产物由 bisheng --cce-enable-sanitizer 编出（含嵌入 device ELF）。
    0x234 对应 DataMover::Process() 中 SetFlag 调用（flag_oob.asc:22），
    即 inline 链最内层的用户源帧；外层调用点 op.Process()（:39）不作为主行。
    """

    OBJ = "examples/synccheck/flag_oob/build/flag_oob_san.asc.o"

    def setUp(self):
        import os

        if not os.path.isfile(self.OBJ):
            self.skipTest("flag_oob_san.asc.o not built")

    def test_symbolize_offending_pc(self):
        m = PcSourceMapper(self.OBJ)
        if not m.available:
            self.skipTest("source-mapping tools unavailable")
        m.build({"mix_mode": [0x9000D0D020, 0x9000D0D234]})
        loc = m.lookup("mix_mode", 0x9000D0D234)
        self.assertIsNotNone(loc, "expected symbolized source loc")
        self.assertTrue(loc.src_file.endswith("flag_oob.asc"))
        self.assertEqual(loc.line, 22)


if __name__ == "__main__":
    unittest.main()
