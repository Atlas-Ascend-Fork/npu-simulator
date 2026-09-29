# -*- coding: utf-8 -*-
#!/usr/bin/env python3
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

"""Connection-local projection of helper instruction facts and derived metrics."""

import json
from typing import Any, Dict

from npusim.prof_demo.database import database_handler


_DEFAULT_VECTOR_LENGTH_BYTES = 256
_SIMT_WARP_LANES = 32
_VECTOR_PARAM_INSTRS = frozenset(
    {
        "RV_VLD",
        "RV_VLDI",
        "RV_VLDS",
        "RV_VST",
        "RV_VSTI",
        "RV_VSTS",
    }
)
_SIMT_MEM_PARAM_INSTRS = frozenset(
    {
        "SIMT_LDG",
        "SIMT_LDS",
        "SIMT_STG",
        "SIMT_STS",
    }
)

# Flat contract keys consumed by current reports.  The projection is analysis-local;
# helper-owned record tables remain read-only throughout report generation.
_PROJECTION_KEYS = (
    "instr_type",
    "core_type",
    "is_wait",
    "is_sync",
    "is_duration_excluded",
    "vf_class",
    "vf_simt_class",
    "disclosure_status",
    "sync_kind",
    "target_pipe",
    "consumer_pipe",
    "buf_id",
    "buf_mode",
    "buf_cnt",
    "flag_id",
    "src_mem",
    "dst_mem",
    "burst_len",
    "burst_number",
    "src_stride",
    "dst_stride",
    "sub_block_id",
    "n",
    "d",
    "m",
    "k",
    "m_start_pos",
    "k_start_pos",
    "m_step",
    "k_step",
    "processed_bytes",
    "processed_elements",
    "vector_utilization",
    "matrix_a_dtype",
    "matrix_b_dtype",
    "matrix_c_dtype",
    "matrix_a_processed_bytes",
    "matrix_b_processed_bytes",
    "matrix_c_processed_bytes",
    "hw_checks_unit_flag",
    "hw_sets_unit_flag",
    "gemv_mode_enable",
    "matrix_c_in_bias_table_buffer",
    "matrix_c_init_val",
    "l2_cache_ctrl_mode",
    "pre_quant_mode",
    "pre_relu_mode",
    "pre_clip_relu_mode",
    "post_relu_mode",
    "post_clip_relu_mode",
    "f32_channel_split_enable",
    "nz2nd_enable",
    "nz2dn_enable",
    "c0_padding_enable",
    "dst_mode",
    "dst_ub_mask",
    "loop0_size",
    "loop1_size",
    "loop2_size",
    "loop3_size",
    "loop4_size",
    "loop0_lp_size",
    "loop0_rp_size",
    "branch_efficiency",
)


def _canonical_metric_number(value: float) -> Any:
    """Match the JSON number shape previously emitted by helper."""
    return int(value) if value.is_integer() else value


def _add_derived_metrics(instr_name: str, values: dict) -> None:
    """Add report metrics derived from decoder facts, without changing DB input."""
    processed_bytes = values.get("processed_bytes")
    if instr_name in _VECTOR_PARAM_INSTRS and processed_bytes is not None:
        values["vector_utilization"] = _canonical_metric_number(
            float(processed_bytes) / float(_DEFAULT_VECTOR_LENGTH_BYTES) * 100.0
        )

    exec_mask = values.get("exec_mask")
    prdct_mask = values.get("prdct_mask")
    if instr_name in _SIMT_MEM_PARAM_INSTRS and exec_mask is not None and prdct_mask is not None:
        active_lanes = bin(int(exec_mask) & int(prdct_mask)).count("1")
        values["branch_efficiency"] = _canonical_metric_number(float(active_lanes) / float(_SIMT_WARP_LANES) * 100.0)


def load_extend_params() -> Dict[int, dict]:
    """Read fresh facts for each report session as ``{ExecInstrId: flat_dict}``."""
    result: Dict[int, dict] = {}
    for row in database_handler.iter_rows(
        "SELECT ExecInstrId, ExecInstrName, ExtendParams FROM sim.ExecutedInstructions"
    ):
        raw = row["ExtendParams"]
        if raw is None:
            raise RuntimeError(
                f"ExecutedInstructions row {row['ExecInstrId']} has no ExtendParams; "
                "re-run cannsim record with a contract v6 helper"
            )
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid ExtendParams JSON on ExecInstrId={row['ExecInstrId']}") from exc
        if not isinstance(value, dict):
            raise RuntimeError(f"ExtendParams on ExecInstrId={row['ExecInstrId']} is not an object")
        required = {
            "instr_type",
            "core_type",
            "is_wait",
            "is_sync",
            "is_duration_excluded",
            "vf_class",
            "vf_simt_class",
        }
        missing = required - value.keys()
        if missing:
            raise ValueError(
                f"Missing ExtendParams keys on ExecInstrId={row['ExecInstrId']}: {', '.join(sorted(missing))}"
            )
        _add_derived_metrics(str(row["ExecInstrName"]), value)
        result[int(row["ExecInstrId"])] = value

    return result


def install_extend_params_projection() -> None:
    """Materialize cached keys in the writable analysis DB for report SQL."""
    params = load_extend_params()
    database_handler.execute("DROP TABLE IF EXISTS main.ExtendParamsProjection")
    columns = ", ".join(_PROJECTION_KEYS)
    database_handler.execute(
        "CREATE TABLE main.ExtendParamsProjection(ExecInstrId INTEGER PRIMARY KEY, " + columns + ")"
    )
    placeholders = ",".join("?" for _ in range(len(_PROJECTION_KEYS) + 1))
    rows = [(exec_id, *(values.get(key) for key in _PROJECTION_KEYS)) for exec_id, values in params.items()]
    database_handler.executemany(
        "INSERT INTO main.ExtendParamsProjection VALUES (" + placeholders + ")",
        rows,
    )

    # Existing analyzers operate on the report-only snapshot.  Preserve their stable
    # column names while replacing the values with the v6 fact/derivation source.
    assignments = {
        "ExecInstrIsWait": "is_wait",
        "ExecInstrIsSynchronization": "is_sync",
        "ExecInstrIsDurationExcluded": "is_duration_excluded",
        "ExecInstrVfClass": "vf_class",
        "ExecInstrVfSimtClass": "vf_simt_class",
    }
    set_clause = ", ".join(
        f"{column}=(SELECT EP.{key} FROM main.ExtendParamsProjection AS EP "
        "WHERE EP.ExecInstrId=main.ExecutedInstructionsAdjusted.ExecInstrId)"
        for column, key in assignments.items()
    )
    database_handler.execute("UPDATE main.ExecutedInstructionsAdjusted SET " + set_clause)
    database_handler.commit()
