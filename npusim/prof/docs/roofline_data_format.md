# Roofline database format

This document describes the persisted Roofline data consumed by report generators
and external UIs. The implemented architecture
is `v100`.

The authoritative definitions are [analysis.sql](../src/sql/analysis.sql).
[RooflineAnalyzer](../src/backend/analyzers/roofline_analyzer.py) computes and writes
the data. [RooflineReporter](../src/frontend/roofline_reporter.py) reads it to produce
a JSON export. This branch does not generate Source/Assembly or Roofline HTML.

For source/assembly views, correlation keys, metrics and archive consumption, see
[Source/Assembly UI data contract](source_assembly_data_format.md).

## Storage and lifecycle

The four `Roofline*` tables reside in the same SQLite database as the kernel's
`ExecutedInstructions`, `Functions`, and other profiler tables. Each kernel-launch
database has its own snapshot. There is no separate Roofline database.

Enable the analyzer through the profiler backend options:

```bash
BIPROF_OPTS='--roofline --object-file /path/to/device.o' \
  npusim report -e /path/to/npusim_archive -n 0
```

`--object-file` supplies the matching device ELF when function metadata must be
loaded. `--roofline` belongs in `BIPROF_OPTS`, rather than the top-level report
arguments. `--smart-pipes` is not required.

A successful analyzer run replaces the rows in all four tables within one
savepoint. The tables contain the latest snapshot, not an append-only history.
The analyzer manager commits the result before presentation runs.

Before consuming a snapshot, check the latest `AnalysisState` entry with
`AnalysisName = 'roofline'`. `AnalysisPassed = 1` indicates success; a failed entry
must not be rendered using rows left from an earlier snapshot. Table existence
alone does not prove that the analyzer ran. A successful analysis can also have
zero points; rerun analysis with DEBUG logging in that case.

## Table overview

| Table | Row meaning | Primary key |
| --- | --- | --- |
| `RooflineMetadata` | Architecture, frequencies, and observed execution-unit counts | `AnalysisId` |
| `RooflinePoints` | One function/mode/memory-level observation | `(FunctionPC, ExecutionMode, MemoryLevel)` |
| `RooflineComputePeaks` | One full-chip compute reference | `(Unit, DataType)` |
| `RooflineBandwidthPeaks` | One full-chip memory-bandwidth reference | `(Unit, MemoryLevel)` |

## Table definitions

### `RooflineMetadata`

Exactly one row is written for a completed snapshot.

| Column | SQLite type | Meaning |
| --- | --- | --- |
| `AnalysisId` | `INTEGER` | Snapshot identifier; the primary key is constrained to `1`. |
| `Architecture` | `TEXT` | Architecture identifier, currently `v100`. |
| `FrequencyGHz` | `REAL` | Positive recorded frequency from `KernelInfo`; retained as metadata. |
| `ReferenceFrequencyGHz` | `REAL` | Positive reference frequency used to convert ticks to seconds; currently `1.65`. |
| `ActiveCoreCount` | `INTEGER` | Number of distinct physical core IDs observed in the selected instruction rows. |
| `MaxCoreCount` | `INTEGER` | Full-chip physical core count from the architecture model; currently `32`. |
| `ActiveAicCount` | `INTEGER` | Number of distinct core IDs with observed AIC instructions. |
| `ActiveAivCount` | `INTEGER` | Number of distinct `(core ID, AIV subcore type)` pairs observed. AIV0 and AIV1 count separately. |

For a recording containing AIC, AIV0, and AIV1 on core 0, the active counts are
`1`, `1`, and `2` respectively. Do not add AIC and AIV counts to obtain the
physical core count. Active counts do not scale the stored reference ceilings.


### `RooflinePoints`

The composite primary key is `(FunctionPC, ExecutionMode, MemoryLevel)`.
A function can therefore produce several points, one for each applicable memory
level. Work and duration are shared between those points; byte counts can differ.

| Column | SQLite type | Meaning / unit |
| --- | --- | --- |
| `FunctionPC` | `INTEGER` | Function entry in bytes relative to the launched kernel entry. Corresponds to `Functions.FunctionAddr` and uses the same coordinates as recorded PCs. Add `KernelInfo.object_entry_pc` to recover the ELF address. |
| `FunctionSize` | `INTEGER` | Function code size in bytes. Its address range is `[FunctionPC, FunctionPC + FunctionSize)`. |
| `FunctionName` | `TEXT` | Function symbol name copied into the snapshot; it may be C++-mangled. |
| `ExecutionMode` | `TEXT` | `SIMD`, `SIMT`, or `CUBE`. |
| `Unit` | `TEXT` | `AIV` for SIMD/SIMT; `AIC` for CUBE. |
| `MemoryLevel` | `TEXT` | Internal memory-level identifier listed below. |
| `ExecutionCount` | `INTEGER` | Number of attributed VF/VFI/VF_SIMT calls for vector modes, summed across selected cores/subcores. CUBE uses `1` for the aggregate function span. |
| `DurationTicks` | `INTEGER` | Positive duration in simulator ticks, using the mode-specific rules below. |
| `FpOperations` | `INTEGER` | Nonnegative weighted FP work according to the BiProf-compatible opcode rules. |
| `IntegerOperations` | `INTEGER` | Nonnegative weighted integer work according to the same rules. |
| `TotalOperations` | `INTEGER` | Positive `FpOperations + IntegerOperations`; the numerator used for both coordinates. |
| `ProcessedBytes` | `INTEGER` | Positive byte total for this point's memory level. |
| `ArithmeticIntensity` | `REAL` | `TotalOperations / ProcessedBytes`, in operations per byte; the chart's X coordinate. |
| `ThroughputGBs` | `REAL` | `ProcessedBytes / 1024^3 / seconds`, in GiB/s despite the historical column name. |
| `PerformanceGFlops` | `REAL` | `ArithmeticIntensity * ThroughputGBs`; the chart's Y coordinate. The historical name is retained, but integer work also contributes. |

The schema enforces positive duration, bytes, and total work, nonnegative FP and
integer work, and the equality defining `TotalOperations`.

`FunctionPC` is a logical reference to `Functions.FunctionAddr`; there is no SQL
foreign-key constraint. Function identity fields are copied into the snapshot,
so a reader can display points without joining static metadata. Instruction
addresses in `ExecutedInstructions` and `SourceInstructions` remain named
`SourceInstrAddr`.

#### Memory levels and byte sources

| `ExecutionMode` | `Unit` | Stored `MemoryLevel` | Debug JSON/HTML label | Byte accounting |
| --- | --- | --- | --- | --- |
| `SIMD` | `AIV` | `SIMD-UB` | `SIMD` | Non-null `VectorUtilizations.ProcessedBytes` for RVECLD/RVECST. Missing join rows contribute nothing. If the table is absent, contract bytes are used only for supported vector load/store opcodes. |
| `SIMT` | `AIV` | `SIMT-UB` | `SIMT` | Prefer `UbRwStatistics` with direction values `1`/`2`, without a cache filter; otherwise use LDS/STS active-lane payload. |
| `SIMT` | `AIV` | `SIMT-OUT` | `SIMT-OUT` | Prefer `SIMTLdStProcessedBytes` for LDG/STG; otherwise use active-lane payload. |
| `CUBE` | `AIC` | `CUBE-L0` | `L0` | BiProf's MTE2/FIXP selection: prefer `DmaMovProcessedBytes` and `FixpParams`, with contract-byte fallback. MTE1 traffic is excluded. |
| `CUBE` | `AIC` | `CUBE-OUT` | `CUBE-OUT` | OUT-to-L1 and L0C/L0C32-to-OUT transfers. FIXP output is divided by two when its destination UB mask is `3`. |

SIMD does not add unsupported scatter-store traffic from `UbRwStatistics` to
BiProf's byte sum. Memory payload counts are not measurements of physical DDR
transactions. Do not add byte totals or work across a function's different
memory-level points as if they were independent computations.

#### Work, duration, and coordinates

Work follows BiProf's opcode conventions rather than a strict FP-only definition.
SIMD VMULA/VMADD and SIMT FFMA/IMAD count twice per active element. SIMD element
counts come from contract facts, with dtype-based reconstruction when needed;
SIMT counts use `popcount(exec_mask & prdct_mask)`. MMAD contributes `2*M*N*K`.
The analyzer implements these counting choices; formulas are documented below.

For SIMD/SIMT, `DurationTicks` is the sum of the **original recorded** `end-start`
values of attributed calls. Concurrent calls on different subcores are summed,
not merged into an interval union; this is not kernel wall-clock latency.
Non-overlapping execution windows are derived to identify callees when VF
commands are queued early. Those narrowed windows do not replace the recorded
durations in the rate denominator, and the analysis does not rewrite timestamps.

For CUBE, duration is `MAX(end)-MIN(start)` over the selected instructions mapped
to the CUBE function, including scalar/control instructions and gaps. It is not
the sum of MMAD instruction durations.

With `O = TotalOperations`, `B = ProcessedBytes`, `T = DurationTicks`, and
`f = RooflineMetadata.ReferenceFrequencyGHz`:

```text
seconds = T / (f * 1e9)
ArithmeticIntensity = O / B
ThroughputGBs = B / 1024^3 / seconds
PerformanceGFlops = ArithmeticIntensity * ThroughputGBs
                  = O / 1024^3 / seconds
```

The debug UI labels performance as `GOP/s`. For exact BiProf compatibility, the
stored actual rate uses the binary `1024^3` divisor. Consumers must preserve that
normalization rather than silently substituting decimal `1e9` or FP-only work.
Use `ReferenceFrequencyGHz` for the conversion, not the recorded `FrequencyGHz`.

### `RooflineComputePeaks`

Primary key: `(Unit, DataType)`.

| Column | SQLite type | Meaning |
| --- | --- | --- |
| `Unit` | `TEXT` | Execution-unit family: `AIV` or `AIC`. |
| `DataType` | `TEXT` | Compute-reference variant listed below. |
| `PeakGFlops` | `REAL` | Full-chip reference value from `peak_tflops_map * 1000`, retained in BiProf's reference convention. |

Current variants are `fp16`, `bf16`, `fp32` for AIV, and `fp16_bf16`, `tf32_hf32`,
`fp32`, `int8_fp8` for AIC. The table contains available reference roofs, not a
measurement of a particular function's dtype.

`RooflinePoints` has no `DataType` column. A consumer chooses which reference
variant(s) to display; the existence of a peak row must not be interpreted as
proof that a point executed that dtype. Reference compute values are not scaled
to `ActiveAicCount` or `ActiveAivCount`.

### `RooflineBandwidthPeaks`

Primary key: `(Unit, MemoryLevel)`.

| Column | SQLite type | Meaning |
| --- | --- | --- |
| `Unit` | `TEXT` | `AIV` or `AIC`. |
| `MemoryLevel` | `TEXT` | Same internal memory-level identifier used in `RooflinePoints`. |
| `PeakGBs` | `REAL` | Full-chip bandwidth ceiling from the architecture constants, in GiB/s. |

Join a point to its bandwidth reference using **both** `Unit` and `MemoryLevel`.
SIMD/SIMT UB and CUBE L0 references use the BiProf path-bandwidth formulas
multiplied by the full physical core count. OUT references use the chip-level
bandwidth directly. Active-core counts do not rescale these values.

For a selected compute reference and matching bandwidth reference, a reader
constructs the roof and ridge point as:

```text
roof(I) = min(PeakGFlops, I * PeakGBs)
ridge_intensity = PeakGFlops / PeakGBs
```



## Reading examples

Check the latest analyzer status, then inspect the point coordinates:

```sql
SELECT AnalysisPassed
FROM AnalysisState
WHERE AnalysisName = 'roofline'
ORDER BY rowid DESC
LIMIT 1;

SELECT FunctionPC, FunctionName, ExecutionMode, Unit, MemoryLevel,
       ExecutionCount, DurationTicks, TotalOperations, ProcessedBytes,
       ArithmeticIntensity AS X, PerformanceGFlops AS Y, ThroughputGBs
FROM RooflinePoints
ORDER BY CASE Unit WHEN 'AIV' THEN 0 ELSE 1 END,
         FunctionPC, MemoryLevel;
```

Retrieve a point's matching memory ceiling:

```sql
SELECT P.FunctionPC, P.ExecutionMode, P.MemoryLevel,
       P.ArithmeticIntensity, P.PerformanceGFlops, B.PeakGBs
FROM RooflinePoints AS P
JOIN RooflineBandwidthPeaks AS B
  ON B.Unit = P.Unit AND B.MemoryLevel = P.MemoryLevel;
```

For the observed falite Softmax SIMD point, `TotalOperations = 82944`,
`ProcessedBytes = 132096`, and `DurationTicks = 1825` give approximately
`0.62791 OP/byte`, `111.23 GiB/s`, and `69.84` in the stored performance units.

## Debug-export naming

`roofline.json` is the data export. The
database fields remain the data contract. The reporter maps memory-level names
using the table above and builds point names as `<FunctionName> - <label>`.
It exports `FunctionPC` as `pc`, `DurationTicks` as `duration_sum`,
`TotalOperations` as `proc_elem_sum`, `ProcessedBytes` as `proc_bytes_sum`, and
`PerformanceGFlops` as `performance`. JSON names and display labels do not rename
the persisted columns or change their units.

## Diagnostics and incomplete facts

Diagnostic counts are emitted by the analyzer at DEBUG log level, not stored in
tables or exported by the reporter. Missing operation or byte facts are skipped
independently, so points may represent partial sums. Points without positive
work, bytes or duration are omitted. Ambiguous VF attribution invalidates the
affected function/mode. Enable DEBUG logging when investigating missing points.
