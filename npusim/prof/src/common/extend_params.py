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

import json
from typing import Any, Optional


class ExtendParams:
    """Wrapper for the ExtendParams JSON column of ExecutedInstructions.

    The contract stores semantic instruction parameters as a flat JSON
    object (keys sorted lexicographically).  Absence of a key means
    "not applicable"; ``null`` never appears in the JSON.

    Usage::

        ep = ExtendParams.parse(row["ExtendParams"])
        if ep.src_addr is not None:
            ...
    """

    __slots__ = ("_data",)

    def __init__(self, data: Optional[dict] = None) -> None:
        self._data: dict = data or {}

    @classmethod
    def parse(cls, raw: Optional[str]) -> "ExtendParams":
        if raw is None or raw == "":
            return cls()
        try:
            return cls(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return cls()

    def raw(self) -> dict:
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    # -- common fields (§6.2) --
    @property
    def instr_type(self) -> Optional[str]:
        return self._data.get("instr_type")

    @property
    def core_type(self) -> Optional[str]:
        return self._data.get("core_type")

    @property
    def queue_type(self) -> Optional[str]:
        return self._data.get("queue_type")

    @property
    def vf_class(self) -> Optional[str]:
        return self._data.get("vf_class")

    @property
    def vf_simt_class(self) -> Optional[str]:
        return self._data.get("vf_simt_class")

    @property
    def is_wait(self) -> Optional[int]:
        return self._data.get("is_wait")

    @property
    def is_sync(self) -> Optional[int]:
        return self._data.get("is_sync")

    @property
    def is_duration_excluded(self) -> Optional[int]:
        return self._data.get("is_duration_excluded")

    # -- SYNC_BUF (§7.1) --
    @property
    def sync_kind(self) -> Optional[str]:
        return self._data.get("sync_kind")

    @property
    def target_pipe(self) -> Optional[str]:
        return self._data.get("target_pipe")

    @property
    def buf_id(self) -> Optional[int]:
        return self._data.get("buf_id")

    @property
    def buf_mode(self) -> Optional[int]:
        return self._data.get("buf_mode")

    @property
    def buf_cnt(self) -> Optional[int]:
        return self._data.get("buf_cnt")

    # -- SYNC_FLAG (§7.2) --
    @property
    def flag_id(self) -> Optional[int]:
        return self._data.get("flag_id")

    @property
    def consumer_pipe(self) -> Optional[str]:
        return self._data.get("consumer_pipe")

    # -- DMA_ALIGN / DMA_MULTI / DMA_2D / DMA_UB / DMA_ND / DMA_3D (§7.3–7.5, 7.9–7.11) --
    @property
    def src_mem(self) -> Optional[str]:
        return self._data.get("src_mem")

    @property
    def dst_mem(self) -> Optional[str]:
        return self._data.get("dst_mem")

    @property
    def src_addr(self) -> Optional[int]:
        return self._data.get("src_addr")

    @property
    def dst_addr(self) -> Optional[int]:
        return self._data.get("dst_addr")

    @property
    def burst_len(self) -> Optional[int]:
        return self._data.get("burst_len")

    @property
    def burst_number(self) -> Optional[int]:
        return self._data.get("burst_number")

    @property
    def src_stride(self) -> Optional[int]:
        return self._data.get("src_stride")

    @property
    def dst_stride(self) -> Optional[int]:
        return self._data.get("dst_stride")

    @property
    def processed_bytes(self) -> Optional[int]:
        return self._data.get("processed_bytes")

    @property
    def sub_block_id(self) -> Optional[int]:
        return self._data.get("sub_block_id")

    # -- DMA_MULTI (§7.4) --
    @property
    def n(self) -> Optional[int]:
        return self._data.get("n")

    @property
    def d(self) -> Optional[int]:
        return self._data.get("d")

    # -- DMA_2D / DMA_3D (§7.5, 7.11) --
    @property
    def m_start_pos(self) -> Optional[int]:
        return self._data.get("m_start_pos")

    @property
    def k_start_pos(self) -> Optional[int]:
        return self._data.get("k_start_pos")

    @property
    def m_step(self) -> Optional[int]:
        return self._data.get("m_step")

    @property
    def k_step(self) -> Optional[int]:
        return self._data.get("k_step")

    # -- DMA_ND (§7.10) --
    @property
    def loop0_size(self) -> Optional[int]:
        return self._data.get("loop0_size")

    @property
    def loop1_size(self) -> Optional[int]:
        return self._data.get("loop1_size")

    @property
    def loop2_size(self) -> Optional[int]:
        return self._data.get("loop2_size")

    @property
    def loop3_size(self) -> Optional[int]:
        return self._data.get("loop3_size")

    @property
    def loop4_size(self) -> Optional[int]:
        return self._data.get("loop4_size")

    @property
    def loop0_lp_size(self) -> Optional[int]:
        return self._data.get("loop0_lp_size")

    @property
    def loop0_rp_size(self) -> Optional[int]:
        return self._data.get("loop0_rp_size")

    # -- CUBE (§7.7) --
    @property
    def m(self) -> Optional[int]:
        return self._data.get("m")

    @property
    def k(self) -> Optional[int]:
        return self._data.get("k")

    @property
    def k_align(self) -> Optional[int]:
        return self._data.get("k_align")

    @property
    def matrix_a_dtype(self) -> Optional[str]:
        return self._data.get("matrix_a_dtype")

    @property
    def matrix_b_dtype(self) -> Optional[str]:
        return self._data.get("matrix_b_dtype")

    @property
    def matrix_c_dtype(self) -> Optional[str]:
        return self._data.get("matrix_c_dtype")

    @property
    def matrix_a_processed_bytes(self) -> Optional[int]:
        return self._data.get("matrix_a_processed_bytes")

    @property
    def matrix_b_processed_bytes(self) -> Optional[int]:
        return self._data.get("matrix_b_processed_bytes")

    @property
    def matrix_c_processed_bytes(self) -> Optional[int]:
        return self._data.get("matrix_c_processed_bytes")

    @property
    def c_ctrl(self) -> Optional[int]:
        return self._data.get("c_ctrl")

    @property
    def shift_ctrl(self) -> Optional[int]:
        return self._data.get("shift_ctrl")

    # -- FIXP (§7.8) --
    @property
    def quant_pre(self) -> Optional[int]:
        return self._data.get("quant_pre")

    @property
    def quant_post(self) -> Optional[int]:
        return self._data.get("quant_post")

    @property
    def relu_pre(self) -> Optional[int]:
        return self._data.get("relu_pre")

    @property
    def relu_post(self) -> Optional[int]:
        return self._data.get("relu_post")

    @property
    def clip_pre(self) -> Optional[int]:
        return self._data.get("clip_pre")

    @property
    def clip_post(self) -> Optional[int]:
        return self._data.get("clip_post")

    # -- SYNC_FFTS (§7.12) --
    @property
    def ffts_flag_id(self) -> Optional[int]:
        return self._data.get("ffts_flag_id")

    @property
    def ffts_mode(self) -> Optional[int]:
        return self._data.get("ffts_mode")

    # -- SYNC_INTRA_BLOCK (§7.13) --
    @property
    def sync_id(self) -> Optional[int]:
        return self._data.get("sync_id")

    # -- SIMT_MEM (§7.14) --
    @property
    def access_type(self) -> Optional[str]:
        return self._data.get("access_type")

    @property
    def access_btype(self) -> Optional[str]:
        return self._data.get("access_btype")

    @property
    def prdct_mask(self) -> Optional[int]:
        return self._data.get("prdct_mask")

    @property
    def exec_mask(self) -> Optional[int]:
        return self._data.get("exec_mask")

    @property
    def branch_efficiency(self) -> Optional[int]:
        return self._data.get("branch_efficiency")
