# Speed of Light data format

This document describes the Speed of Light (SOL) data written by the profiler
and consumed by report generators and external UIs.

The authoritative schema is [analysis.sql](../src/sql/analysis.sql).
[SpeedOfLightAnalyzer](../src/backend/analyzers/speed_of_light_analyzer.py)
computes and writes the data. The `.npu-rep` archive includes the
`SpeedOfLightData` table as CSV when non-empty.

For feature selection and collection dependencies, see the
[feature dependency map](command_line_features.md).

## Storage and lifecycle

The `SpeedOfLightData` table resides in the same SQLite database as the
kernel's `ExecutedInstructions`, `Functions`, and other profiler tables. Each
kernel-launch database has its own snapshot. There is no separate SOL
database.

SOL is included in the default feature list. To select only SOL:

```bash
npusim record ./app -s Ascend950 -n 0 -g --feature-list sol
npusim report -e /path/to/npusim_archive -n 0 --feature-list sol
```

The `sol` feature requires no collection categories beyond the base set
(`instr,ccu,simt,spr`). DDR SOL additionally requires the helper to supply
`DdrStateSamples` and its sample count in the capture (data contract version
8); captures without DDR samples still report CUBE and VECTOR SOL. See the
[feature dependency map](command_line_features.md).

A successful analyzer run replaces the rows in the table. The table contains
the latest snapshot, not an append-only history. The analyzer manager commits
the result before presentation runs.

Before consuming a snapshot, check the latest `AnalysisState` entry with
`AnalysisName = 'speed_of_light'`. `AnalysisPassed = 1` indicates success; a
failed entry must not be rendered using rows left from an earlier snapshot.
Table existence alone does not prove that the analyzer ran.

## Table definition

### `SpeedOfLightData`

One row per metric. Only metrics that could be computed are present: when the
DDR calculation is unavailable, the `MEMORY` row is absent while the compute
rows are still written. A value is never stored as 0 to mean "not measured".

| Column | SQLite type | Meaning |
| --- | --- | --- |
| `SolType` | `VARCHAR(8)` | Metric identifier: `CUBE`, `VECTOR`, or `MEMORY`. |
| `SolThroughput` | `REAL` | Throughput as a percentage of the corresponding hardware peak. |

The schema enforces non-null values and the primary key on `SolType`, so each
metric appears at most once per snapshot.

## Methodology

All three metrics use the same normalization: an **ideal** amount of work
divided by the **actual** time it took, expressed as a percentage of the
hardware peak. The work counting is deliberately conservative — it is the
minimum the hardware could possibly have spent.

### CUBE

Per core with MMAD work, the ideal cycle count is:

```text
ideal_mmad_cyc = SUM over MMAD instructions of (M * N * K / peak_mac(dtype))
```

The per-cycle MAC maximums come from `peak_cube_mac_per_cycle_map`; the dtype
is taken from `MmadParams.MatrixADtype`. The dtype of Matrix A is used as the
best indicator of the cycles necessary, as Matrix B is always converted into a
dtype of a size matching Matrix A.

| `MatrixADtype` contains | Peak MACs per cycle |
| --- | --- |
| `32` | 512 |
| `16` | 4096 |
| `8` | 8192 |
| `4` | 16384 |

Denominator: the kernel execution time summed over every core that performed
MMAD work. Cores without MMAD instructions contribute nothing to the
denominator.

```text
CUBE SOL = ideal_mmad_cyc / summed_kernel_time * 100
```

An unsupported dtype string raises a `ValueError` and fails the analysis.

### VECTOR

Per core and per AIV subcore type (AIV0 and AIV1 count separately), the ideal
instruction count is:

```text
ideal = SIMD_count / peak_ipc_SIMD + SIMT_count / peak_ipc_SIMT
```

`SIMD_count` is the number of RVECEX instructions whose name does not start
with `SIMT_`; only compute instructions count. `SIMT_count` is the number of
all `SIMT_*` instructions, because a compute-only SIMT maximum cannot be
determined. The IPC maximums come from `peak_ipc_map`.

Denominator: the kernel execution time summed over every (core, subcore type)
pair that has any SIMD or SIMT work.

```text
VECTOR SOL = ideal / summed_kernel_time * 100
```

If no AIV subcore is active, the VECTOR value is 0.

### MEMORY

The memory metric is derived from `DdrStateSamples` rows, which the helper
samples on the DFI clock (`tDFI`, from `dfi_clk_ns_map`), not the AI core
clock. The metric is unavailable — with no row written — when:

- the `DdrStateSamples` table is absent or has no rows;
- the sample count in `KernelInfo` (`ddr_state_sample_count`) is missing, zero
  or unparsable — this covers `STATE_TIME == 0` and old helpers;
- no interval carries DDR traffic;
- the resulting sampling duration is non-positive.

No clipping to the kernel's instruction tick window is applied: the two
tables tick in different clock domains (`ExecutedInstructions` on the AI core
clock, `DdrStateSamples` on the DFI clock), so their tick values must not be
compared. Each contract database is a single kernel launch, and the active
window logic below already trims pre- and post-kernel idle time.

A data self-check counts rows whose `DataTotalBytes` exceeds the per-channel
interval bound `v100_ddr_per_channel_interval_bytes`; such rows indicate a
missing counter reset or an over-long interval, and the analysis logs a
warning but proceeds.

Rows are grouped per interval:

- bytes are summed across all DDR channels and controller instances
  (`SUM(DataTotalBytes)`);
- duration is taken once per interval using `MAX(IntervalEndTick -
  IntervalStartTick)`. All channels share the same timeline — summing
  per-channel tick spans would dilute SOL by the channel count. `MAX` is used
  because interval boundaries are only guaranteed non-overlapping per channel.

The **active window** spans from the first to the last interval with traffic,
interior zero intervals included. Duration is the sum of the actual tick spans
of the window's intervals — no constant-interval assumption is made (dropped
samples make intervals a multiple of `STATE_TIME`). The peak reference is the
chip-level `peak_bandwidth_map["OUT"]` bandwidth:

```text
seconds   = summed_duration_ticks * tDFI_ns * 1e-9
MEMORY SOL = total_bytes / seconds / peak_bandwidth_Bps * 100
```

## Reading examples

Check the latest analyzer status, then read the metrics:

```sql
SELECT AnalysisPassed
FROM AnalysisState
WHERE AnalysisName = 'speed_of_light'
ORDER BY rowid DESC
LIMIT 1;

SELECT SolType, SolThroughput
FROM SpeedOfLightData;
```

Rows for metrics that could not be computed are simply missing; test for
their presence rather than for zero.

## Diagnostics and caveats

The analyzer logs at DEBUG/WARNING level when input facts are missing. Note
in particular:

- The compute denominators sum the kernel time per participating core (or
  subcore type), not an interval-union wall-clock span. A kernel running
  concurrently on several cores is measured against several copies of the
  kernel time.
- CUBE work is weighted by the `MatrixADtype` of each instruction; a kernel
  mixing dtypes is compared against the peak of each instruction's own dtype,
  not a single chip-wide peak.
- The MEMORY metric measures DDR traffic only, on a different clock domain
  from the compute metrics; CUBE/VECTOR and MEMORY percentages must not be
  combined into a single utilization figure.
- A `MEMORY` row absent from the table means the DDR data was not collected
  or was invalid — not that memory utilization was zero.
