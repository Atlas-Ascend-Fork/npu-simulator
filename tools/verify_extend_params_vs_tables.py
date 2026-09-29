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

"""Compare contract-v5 ExtendParams with the retained dual-write tables."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


LEGACY_WHITELIST = (
    "GET_BUF",
    "RLS_BUF",
    "BAR",
    "SET_FLAG",
    "WAIT_FLAG",
    "MOV_SRC_TO_DST_ALIGNv2",
    "MOV_OUT_TO_L1_MULTI_ND2NZ",
    "MOV_OUT_TO_L1_MULTI_DN2NZ",
    "LOAD_2Dv2",
    "LOAD_MX_2Dv2",
    "RV_VLD",
    "RV_VLDI",
    "RV_VLDS",
    "RV_VST",
    "RV_VSTI",
    "RV_VSTS",
    "VF",
    "VFI",
    "MMAD",
    "MMAD_MX",
    "FIX_L0C_TO_DST",
)
DUAL_WRITE_MNEMONICS = LEGACY_WHITELIST + ("SIMT_LDG", "SIMT_STG")

VF_CLASS = {1: "SCALAR", 2: "EXECUTION", 3: "LOAD_STORE", 4: "BRANCH"}

# table -> ((ExtendParams key, legacy column), ...)
TABLE_MAPPINGS: Mapping[str, Sequence[Tuple[str, str]]] = {
    "SyncPublicParams": (
        ("sync_kind", "SyncKind"),
        ("target_pipe", "TargetPipe"),
        ("buf_id", "BufferId"),
        ("buf_cnt", "OccupancyCount"),
        ("flag_id", "FlagId"),
        ("consumer_pipe", "ConsumerPipe"),
    ),
    "DmaMovSimpleParams": (
        ("burst_len", "BurstLength"),
        ("burst_number", "BurstNumber"),
        ("src_stride", "SrcStride"),
        ("dst_stride", "DstStride"),
        ("sub_block_id", "SubBlockId"),
    ),
    "DmaMovProcessedBytes": (
        ("processed_bytes", "ProcessedBytes"),
        ("memory_traffic", "MemoryTraffic"),
        ("cache_hit", "CacheHit"),
    ),
    "DmaMovSpaces": (("src_mem", "SrcSpace"), ("dst_mem", "DstSpace")),
    "DmaMovMultiParams": (("n", "N"), ("d", "D")),
    "DmaMovLoad2dParams": (
        ("m_start_pos", "MStartPosition"),
        ("k_start_pos", "KStartPosition"),
        ("m_step", "MStep"),
        ("k_step", "KStep"),
    ),
    "VectorUtilizations": (
        ("processed_bytes", "ProcessedBytes"),
        ("processed_elements", "ProcessedElements"),
        ("vector_utilization", "VectorUtilization"),
    ),
    "MmadParams": (
        ("m", "M"),
        ("k", "K"),
        ("n", "N"),
        ("matrix_a_dtype", "MatrixADtype"),
        ("matrix_b_dtype", "MatrixBDtype"),
        ("matrix_c_dtype", "MatrixCDtype"),
        ("hw_checks_unit_flag", "HwChecksUnitFlag"),
        ("hw_sets_unit_flag", "HwSetsUnitFlag"),
        ("gemv_mode_enable", "GEMVModeEnable"),
        ("matrix_c_in_bias_table_buffer", "MatrixCInBiasTableBuffer"),
        ("matrix_c_init_val", "MatrixCInitVal"),
        ("matrix_a_processed_bytes", "MatrixAProcessedBytes"),
        ("matrix_b_processed_bytes", "MatrixBProcessedBytes"),
        ("matrix_c_processed_bytes", "MatrixCProcessedBytes"),
    ),
    "FixpParams": (
        ("n", "N"),
        ("m", "M"),
        ("l2_cache_ctrl_mode", "L2CacheCtrlMode"),
        ("hw_checks_unit_flag", "HwChecksUnitFlag"),
        ("hw_sets_unit_flag", "HwSetsUnitFlag"),
        ("pre_quant_mode", "PreQuantMode"),
        ("pre_relu_mode", "PreReluMode"),
        ("pre_clip_relu_mode", "PreClipReluMode"),
        ("f32_channel_split_enable", "F32ChannelSplitEnable"),
        ("post_clip_relu_mode", "PostClipReluMode"),
        ("post_relu_mode", "PostReluMode"),
        ("nz2nd_enable", "NZ2NDEnable"),
        ("nz2dn_enable", "NZ2DNEnable"),
        ("c0_padding_enable", "C0PaddingEnable"),
        ("dst_mode", "DstMode"),
        ("dst_ub_mask", "DstUBMask"),
        ("processed_bytes", "ProcessedBytes"),
    ),
    "SIMTLdStProcessedBytes": (("processed_bytes", "ProcessedBytes"),),
}

# key -> key whose presence gates production of it in ExtendParams.
#
# The retained v4 FixpParams writer binds DstUBMask unconditionally, so a
# Dst=OUT instruction stores the default value 0 even though dst_mode is not
# applicable.  Contract v5 makes dst_ub_mask optional and emits it only when
# dst_mode is present (Dst=UB).  The trace consumer likewise treats the legacy
# value 0 and an absent key identically.  Skip gated values when their gate is
# absent rather than treating this v4 placeholder as a contract mismatch.
GATED_KEYS: Mapping[str, str] = {
    "dst_ub_mask": "dst_mode",
}

SYNC_MNEMONICS = frozenset(LEGACY_WHITELIST[:5])
DMA_ALIGN_MNEMONICS = frozenset(("MOV_SRC_TO_DST_ALIGNv2",))
DMA_MULTI_MNEMONICS = frozenset(("MOV_OUT_TO_L1_MULTI_ND2NZ", "MOV_OUT_TO_L1_MULTI_DN2NZ"))
DMA_2D_MNEMONICS = frozenset(("LOAD_2Dv2", "LOAD_MX_2Dv2"))
DMA_MNEMONICS = DMA_ALIGN_MNEMONICS | DMA_MULTI_MNEMONICS | DMA_2D_MNEMONICS
VECTOR_MNEMONICS = frozenset(("RV_VLD", "RV_VLDI", "RV_VLDS", "RV_VST", "RV_VSTI", "RV_VSTS", "VF", "VFI"))
CUBE_MNEMONICS = frozenset(("MMAD", "MMAD_MX"))
FIXP_MNEMONICS = frozenset(("FIX_L0C_TO_DST",))
SIMT_MEM_MNEMONICS = frozenset(("SIMT_LDG", "SIMT_STG"))
TABLE_MNEMONICS = {
    "SyncPublicParams": SYNC_MNEMONICS,
    "DmaMovSimpleParams": DMA_ALIGN_MNEMONICS,
    "DmaMovProcessedBytes": DMA_MNEMONICS,
    "DmaMovSpaces": DMA_MNEMONICS | FIXP_MNEMONICS,
    "DmaMovMultiParams": DMA_MULTI_MNEMONICS,
    "DmaMovLoad2dParams": DMA_2D_MNEMONICS,
    "VectorUtilizations": VECTOR_MNEMONICS,
    "MmadParams": CUBE_MNEMONICS,
    "FixpParams": FIXP_MNEMONICS,
    "SIMTLdStProcessedBytes": SIMT_MEM_MNEMONICS,
}


def values_equal(left: Any, right: Any, rel_tol: float) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    if isinstance(left, float) or isinstance(right, float):
        try:
            return math.isclose(float(left), float(right), rel_tol=rel_tol, abs_tol=1e-12)
        except (TypeError, ValueError):
            return False
    return left == right


def table_names(connection: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def load_legacy_rows(
    connection: sqlite3.Connection,
    table: str,
) -> Dict[int, sqlite3.Row]:
    return {int(row["ExecInstrId"]): row for row in connection.execute(f"SELECT * FROM {table}")}


def add_difference(
    differences: List[str],
    exec_id: int,
    mnemonic: str,
    key: str,
    json_value: Any,
    legacy_value: Any,
    source: str,
) -> None:
    differences.append(
        f"ExecInstrId={exec_id} {mnemonic} key={key}: ExtendParams={json_value!r}, {source}={legacy_value!r}"
    )


def compare_database(db_path: Path, rel_tol: float) -> Tuple[int, List[str]]:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    differences: List[str] = []
    try:
        present = table_names(connection)
        required = {
            "ExecutedInstructions",
            "InstrTypes",
            "CoreTypes",
            "InstrQueueTypes",
            "SourceInstructions",
            "InstructionDisclosureState",
            *TABLE_MAPPINGS,
        }
        missing = sorted(required - present)
        if missing:
            return 0, ["missing required table(s): " + ", ".join(missing)]

        placeholders = ",".join("?" for _ in DUAL_WRITE_MNEMONICS)
        rows = connection.execute(
            f"""
            SELECT EI.*,
                   IT.InstrTypeName AS LegacyInstrType,
                   CT.CoreTypeName AS LegacyCoreType,
                   IQ.InstrQueueTypeName AS LegacyQueueType,
                   IDS.PolicyVersion AS LegacyPolicyVersion,
                   IDS.Status AS LegacyDisclosureStatus
            FROM ExecutedInstructions AS EI
            LEFT JOIN InstrTypes AS IT ON EI.InstrTypeId=IT.InstrTypeId
            LEFT JOIN CoreTypes AS CT ON EI.CoreTypeId=CT.CoreTypeId
            LEFT JOIN SourceInstructions AS SI
              ON EI.SourceInstrAddr=SI.SourceInstrAddr
             AND SI.SourceInstrSection=1
            LEFT JOIN InstrQueueTypes AS IQ
              ON SI.SourceInstrQueueType=IQ.InstrQueueTypeId
            LEFT JOIN InstructionDisclosureState AS IDS
              ON EI.ExecInstrId=IDS.ExecInstrId
            WHERE EI.ExecInstrName IN ({placeholders})
            ORDER BY EI.ExecInstrId
            """,
            DUAL_WRITE_MNEMONICS,
        ).fetchall()
        legacy_tables = {table: load_legacy_rows(connection, table) for table in TABLE_MAPPINGS}

        for row in rows:
            exec_id = int(row["ExecInstrId"])
            mnemonic = str(row["ExecInstrName"])
            try:
                params = json.loads(row["ExtendParams"])
            except (TypeError, json.JSONDecodeError) as exc:
                differences.append(f"ExecInstrId={exec_id} {mnemonic}: invalid ExtendParams: {exc}")
                continue
            if not isinstance(params, dict):
                differences.append(f"ExecInstrId={exec_id} {mnemonic}: ExtendParams is not an object")
                continue

            universal = {
                "instr_type": row["LegacyInstrType"],
                "core_type": row["LegacyCoreType"],
                "queue_type": row["LegacyQueueType"],
                "is_wait": bool(row["ExecInstrIsWait"]),
                "is_sync": bool(row["ExecInstrIsSynchronization"]),
                "is_duration_excluded": bool(row["ExecInstrIsDurationExcluded"]),
                "vf_class": VF_CLASS.get(row["ExecInstrVfClass"], "NONE"),
                "vf_simt_class": VF_CLASS.get(row["ExecInstrVfSimtClass"], "NONE"),
                "policy_version": row["LegacyPolicyVersion"],
                "disclosure_status": row["LegacyDisclosureStatus"],
            }
            for key, legacy_value in universal.items():
                json_value = params.get(key)
                if not values_equal(json_value, legacy_value, rel_tol):
                    add_difference(
                        differences,
                        exec_id,
                        mnemonic,
                        key,
                        json_value,
                        legacy_value,
                        "legacy columns",
                    )

            for table, mappings in TABLE_MAPPINGS.items():
                if mnemonic not in TABLE_MNEMONICS[table]:
                    continue
                legacy_row = legacy_tables[table].get(exec_id)
                relevant = any(key in params for key, _ in mappings)
                if relevant and legacy_row is None:
                    differences.append(f"ExecInstrId={exec_id} {mnemonic}: missing {table} row")
                    continue
                if legacy_row is None:
                    continue
                for key, column in mappings:
                    gate_key = GATED_KEYS.get(key)
                    if gate_key is not None and gate_key not in params:
                        continue
                    json_present = key in params
                    legacy_value = legacy_row[column]
                    if not json_present and legacy_value is None:
                        continue
                    json_value = params.get(key)
                    if not values_equal(json_value, legacy_value, rel_tol):
                        add_difference(
                            differences,
                            exec_id,
                            mnemonic,
                            key,
                            json_value,
                            legacy_value,
                            f"{table}.{column}",
                        )
        return len(rows), differences
    finally:
        connection.close()


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path, help="helper-generated SQLite DB")
    parser.add_argument(
        "--rel-tol",
        type=float,
        default=1e-9,
        help="relative tolerance for REAL values (default: 1e-9)",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    if not args.database.is_file():
        print(f"error: database does not exist: {args.database}", file=sys.stderr)
        return 2
    checked, differences = compare_database(args.database, args.rel_tol)
    if differences:
        print(f"ExtendParams reconciliation failed: {len(differences)} difference(s)")
        for difference in differences:
            print(f"- {difference}")
        return 1
    print(f"ExtendParams reconciliation passed: {checked} dual-write row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
