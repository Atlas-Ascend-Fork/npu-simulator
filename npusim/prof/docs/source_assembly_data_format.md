# Source/Assembly data contract for UI consumers

This document describes the data available to a Source/Assembly UI in
`feature/roofline-data`. The backend generates SQLite data and `.npu-rep`;
it does not generate a Source/Assembly HTML page in this branch.

The SQL definitions are authoritative:

- [bridge.sql](../src/sql/bridge.sql): source mapping and aggregate views.
- [analysis.sql](../src/sql/analysis.sql): analysis tables and IPC views.
- [hints.sql](../src/sql/hints.sql): performance hints.
- [elf_object_reader.py](../src/backend/elf_object_reader.py): ELF/debug/source ingestion.
- [export_csv.py](../tools/export_csv.py) and
  [npu_rep_export.py](../../core/report/npu_rep_export.py): archive export.

Related contracts: [Roofline](roofline_data_format.md) and
[memory heatmap](memory_heatmap_format.md).

## Inputs and availability

Record with a matching helper. Direct binary recordings archive device ELFs
in `record/aicore_objects`; report resolves the matching object automatically.
An explicit `--object-file /path/to/device.o` can override the archive.
Compile that ELF with `-g` to obtain source mappings. Optimized builds can still
have missing or approximate source positions even with `-g`.

The backend reads source files at the absolute paths recorded in debug info.
Those files, including SDK headers, must exist when generating the report.
Rebuild after changing/removing a toolkit; a previous ELF can point to deleted
headers. Do not silently substitute a different SDK header at the same line.

One DB/archive belongs to one kernel launch. Treat IDs as local to that DB;
include a report/launch identity in UI keys when multiple reports are open.
Optional metrics depend on collected facts, enabled analyzers and CLI flags.
A column existing in the schema does not guarantee that its values are available.

## Data sources by UI element

| UI element | Primary input | Purpose |
|---|---|---|
| Assembly listing and metric cells | `AsmMetrics` | Static PCs with aggregated dynamic metrics |
| Source line metric cells | `SourceLineMetrics` | Aggregation by `(FileId, LineNum)` |
| Source file list | `SourceFiles` | File identity and debug path |
| Full source text | `SourceLines` | Text keyed by file ID and 1-based line number |
| Bidirectional Source/Assembly selection | `SourceInstructions` + `DebugInfo` | PC to file/line mapping, independent of execution |
| Function labels/ranges | `Functions` | `FunctionAddr`, `FunctionSize`, `FunctionName` |
| Basic block labels/edges | `BasicBlocks`, `BasicBlocksRelations` | Block starts, lengths and CFG edges |
| Instruction type filter | `ActiveInstrTypes`, `InstrTypes` | Types actually executed; ID/name dictionary |
| Performance Hints | `InstructionHintsView`, `SourceLineHintsView`, `KernelHintsView` | Instruction, line and kernel hints |
| Optional call hierarchy | `CallStackIDs`, `CallStacks`, `CallFunctions`, `CallGraph`, `CallGraphMetrics` | Stack paths and call metrics |
| Operator summary | `KernelInfo`, `summary.json` | Identity, frequency, counts and timing |
| Summary diagrams | Histogram views listed below | Chart series |

Do not reconstruct Source/Assembly metrics from tracing JSON if the aggregate
views are available. Tracing describes individual executions; these views use
a different granularity.

## Identity, addresses and source text

| Field | Meaning |
|---|---|
| `SourceInstructions.SourceInstrAddr` / `AsmMetrics.PC` | Launch-relative instruction address, matching `ExecutedInstructions.SourceInstrAddr`; display as hexadecimal |
| `ExecutedInstructions.ExecInstrId` | Composite identity of one recorded execution; not a source line or PC |
| `SourceFiles.SourceFileId` / `FileId` | File identity; use the full path for disambiguating basenames |
| `SourceLines.SourceLineNum` / `DebugInfo.DebugInfoLineNum` / `LineNum` | Actual **1-based** source line number |
| `SourceLines.SourceLineId` / `LineId` | Identity of one source position |
| `DebugInfoId` | Mapping-record identity; **not** a source line number |

ELF addresses are translated when importing the selected kernel and its functions.
`KernelInfo.object_entry_pc` records the selected ELF entry address; convert a
report PC to an ELF PC with `elf_pc = report_pc + object_entry_pc`. The inverse
translation is applied to instructions, functions and basic blocks at import.
Recorded PCs and `KernelInfo.start_pc` retain their helper meanings:
`runtime_pc = start_pc + report_pc`. Older reports with zero-entry kernels have
the same numeric PCs; an absent `object_entry_pc` is treated as zero by the symbolizer.

For example, the second kernel can start at ELF PC `0x244` while its first
recorded instruction is PC `0`. Both SA and Roofline use PC `0` for that launch;
external ELF/DWARF queries use `0x244`. Instruction text remains disassembler
output and may contain ELF addresses in labels; use numeric PC columns for joins.

`SourceInstructions` has primary key `(SourceInstrSection, SourceInstrAddr)`.
The current aggregate views group by PC alone. Reports assume their relevant
instruction addresses are unambiguous across sections; consumers handling more
general multi-section ELFs must account for this limitation.

The mapping chain is:

```text
SourceInstructions.DebugInfoId → DebugInfo.DebugInfoId
DebugInfo.SourceFileId        → SourceFiles.SourceFileId
DebugInfo.SourceLineId        → SourceLines.SourceLineId
SourceLines.SourceFileId      → SourceFiles.SourceFileId
```

Read `SourceLines` by file ID, ordered by `SourceLineNum`, to reconstruct a
file. For a readable file this includes comments, blank lines and lines
without an instruction. `DebugInfo` provides the locations from the ELF.
Never interpret an absolute producer path as permission to read arbitrary files
on the user's machine; source resolution belongs to the host's own policy.

A valid debug location can have `SourceLineId = NULL` because source text was
unavailable. File/line identity may still be useful for navigation or a missing
source message. A nonpositive line number or missing file is not a valid editor
caret target. No debug mapping should mean “unmapped”, not line 1.

### Mapping query for both directions

```sql
SELECT s.SourceInstrSection AS SectionId,
       s.SourceInstrAddr AS PC,
       s.SourceInstrEncoding AS Encoding,
       s.SourceInstrText AS Assembly,
       d.SourceFileId AS FileId,
       f.SourceFilePath AS FilePath,
       d.DebugInfoLineNum AS LineNum,
       d.SourceLineId AS LineId,
       l.SourceLine AS LineText
FROM SourceInstructions AS s
LEFT JOIN DebugInfo AS d ON d.DebugInfoId = s.DebugInfoId
LEFT JOIN SourceFiles AS f ON f.SourceFileId = d.SourceFileId
LEFT JOIN SourceLines AS l ON l.SourceLineId = d.SourceLineId
ORDER BY s.SourceInstrSection, s.SourceInstrAddr;
```

Build `PC → (FileId, LineNum)` and `(FileId, LineNum) → PCs` from these rows.
A source line can map to many instructions. Changing the source file should
not discard instructions belonging to other files from a shared listing.
For a click on an unmapped instruction, clear the previous correlation rather
than leaving an apparently valid stale pair selected.

One static instruction stores one selected `DebugInfoId`. The full DWARF inline
call chain is not represented by this relationship. A PC mapped to an SDK
header does not automatically map to its outer user-code call site.
Functions and call stacks are separate inputs, not substitute source locations.

## Aggregate metrics and display units

`AsmMetrics`: one row per static PC, driven by `SourceInstructions` with a left
join to executions. An unexecuted instruction can still appear, typically with
`ExecutionCount = 0` and other metrics `NULL`.

`SourceLineMetrics`: one row per `(FileId, LineNum)` with dynamic executions;
it is not a row for every line in the file. It uses an inner join to executions.
Its `DebugInfoId` and `LineId` are non-grouped SQL columns: do not use them as
unique identifiers when several debug records share a source position.

These views aggregate across recorded cores/subcores. There is no per-core
selector column in their output. A per-core UI needs a separate query over the
underlying execution tables; filtering the already aggregated view is insufficient.

| Field | Assembly aggregation | Source aggregation | Display meaning |
|---|---|---|---|
| `ExecutionCount` | COUNT executions | COUNT executions | Dynamic instruction count; source count is not the number of line visits |
| `TickCount` | SUM(end − start) | SUM(end − start) | Accumulated instruction ticks; not operator wall time |
| `RelTime` | Log-normalized ticks | Log-normalized ticks, capped at 1 | Dimensionless heat value |
| `UBBankConflicts` | SUM(read + write) | Same | Conflict count |
| `UBCrossInstructionBankConflicts` | SUM | SUM | Cross-instruction conflict count |
| `VectorUtilization` | AVG | AVG | Percent, already 0–100 |
| `ProcessedBytes` | AVG | AVG | Bytes per joined execution sample; **not** total bytes |
| `MemoryTraffic` | AVG rounded to 2 decimals | Same | Backend DMA throughput, binary-GB/s convention (GiB/s), not bytes |
| `DmaSrcStride`, `DmaDstStride`, `BurstNumber` | AVG | AVG | Producer DMA parameters; do not reinterpret as SIMT element stride |
| `CacheLineCount` | AVG | AVG | Cache-line count, can be fractional after averaging |
| `CacheLineUtilization` | AVG | AVG | Percent, already 0–100 |
| `MemAccessStride` | AVG | AVG | SIMT stride in elements |
| `LiveGPRegisters`, `LiveSharedRegisters` | **SUM** | **MAX** | Current view semantics; register counts, not occupancy |
| `BranchEfficiency` | AVG over positive masks | Same | Percent, already 0–100 |
| `WarpOccupancy` | AVG | AVG | Fraction; legacy HTML multiplied by 100 for percent display |
| `ScalarIPC`, `ExecIPC`, `LdStIPC`, `BranchIPC` | AVG rounded to 2 decimals | Not columns of this view | Aggregate IPC from VF and VF_SIMT invocations in `IPCAsmMetrics` |
| `VfSimtTidX/Y/Z`, `VfSimtRegNum` | Invocation metadata | Not columns of this view | Optional launch dimensions / allocated registers |
| `Active`, `Stall`, `EmptyTicks`, `MemoryStall`, `ExecutionStall`, `QueueFullStall`, `SyncStall`, `InstrFetchStall`, `PipeActive` | SUM | SUM | Producer sampling/stall counters; not interchangeable with measured instruction duration |
| `SIMDStallsJson` | `SIMDStallsByAddr.StallsJson` | `SIMDStallsByLine.StallsJson` | Serialized reason-to-value object; parse as JSON |

`CacheLineCount`, `CacheLineUtilization` and `MemAccessStride` are produced by
BiProf's base SIMT memory analyzer from a recording that includes `instr,simt`.
They require per-thread addresses; SIMT IPC and execution masks do not require
that address stream. Base collection includes `simt` automatically unless
`CANNSIM_COLLECT` is explicitly overridden. No separate SIMT-analysis flag is
introduced.

Both views also expose location columns. `AsmMetrics` additionally has
`InstrName`, `InstrParams`, `Pipe` and `CallStackId`. `InstrName` is the stored
instruction text, which can include operands; it is not always a bare mnemonic.
Use `SourceInstructions` for encoding and section identity.

Heat normalization is `ln(0.5 * TickCount + 1) / ln(0.5 * instrs_time_sum + 1)`;
`instrs_time_sum` comes from `KernelInfo`. Handle missing/zero denominators and
non-finite values as unavailable. SQLite connections evaluating these views need
an `LN` function; the backend registers it, while CSV consumers need no SQL runtime.

For timing conversion, if `frequency_ghz` is present and positive:
`nanoseconds = ticks / frequency_ghz`; `microseconds = ticks / (1000 * frequency_ghz)`.
Concurrent instructions overlap: summing their ticks does not yield wall time.

Preserve `NULL` as unavailable (e.g. blank/“—”), and numeric zero as a measured or
stored zero. The legacy HTML adapter replaced many NULLs with `""`; that was a
presentation convention, not the SQL type. AVG is an unweighted SQL average of
joined rows, not a duration-weighted mean. Joins may fan out when an auxiliary
input has multiple records for an execution (notably `IPCAsmMetrics` is a
`UNION ALL`); this document describes existing view results, not a guarantee
that their counts equal an independently recomputed raw-execution count.

## Functions, blocks, stacks and hints

Function membership uses `FunctionAddr <= PC < FunctionAddr + FunctionSize`.
Aliases/overlapping function ranges can exist: do not assume one unique name
for every PC. `BasicBlocksRelations` stores block-start addresses as
`StartBlockId` and `EndBlockId`; these are not dynamic execution IDs.

`CallStackIDs` attaches one `CallStackId` to a static PC. Join it to
`CallStacks.StackId`, then `CallFunctions` and `SourceFiles`, sorting frames by
`StackLevel`. This is not a dynamically reconstructed stack per execution.
Call-stack analysis is optional (`--call-stack`); the absence of stack rows must
not prevent Source/Assembly from displaying available code and line mappings.

For the Performance Hints table:

```sql
SELECT PC, FileId, LineNum, HintTypeId, HintMsgText
FROM InstructionHintsView
ORDER BY PC;

SELECT FileId, LineNum, HintMsgText
FROM SourceLineHintsView
ORDER BY FileId, LineNum;
```

Instruction hints target a PC and can also carry a source position; line hints
target `(FileId, LineNum)`. Kernel hints have no instruction selection target.
Keep all applicable messages; do not key hints only by a line number without
its file. Hint views currently group by message ID, so they are not necessarily
an exhaustive list of every target sharing that message. Inspect raw hint
tables and their joins if the UI needs all target/message associations.

For dynamic trace links, preserve composite `ExecInstrId` for identity and
joins. Display its original number with `decompose_instr_id(id)[0]` (low 32
bits). `conflict_instructions` in tracing uses the same display conversion;
the original composite IDs remain in `UbBankConflictsCross.ConflictInstrs`.
The plain displayed instruction number is not globally unique across cores.

## Summary charts

| View/table | Chart fields / grouping |
|---|---|
| `InstrTypeHistCount` | `Type`, `Count` |
| `InstrTypeHistClocks` | `Type`, `TickCount` |
| `InstrNameHistCount` | `Name`, `Count` |
| `InstrNameHistClocks` | `Name`, `TickCount` |
| `PipeUtilizationHist` | `PipeName`, `CoreName`, `Utilization` (fraction; percent display ×100) |
| `AiCoreOccupancy` | `CoreId`, `CoreTypeId`, `TickStart`, `TickEnd`; observed span, not a utilization percentage |
| `BasicBlockCountDiagram` | `Name`, `ExeCount`; block-start execution count |
| `KernelInfo` | Overall scalar IPC in the `scalar_ipc` entry when available |
| `VfIPC`, `VfSimtIPC` | Aggregate IPC for each VF or VF_SIMT invocation |

Read frequency and operator timing from `KernelInfo`; `summary.json` provides
additional prepared summary series. Do not substitute instruction-time sums
for operator duration or infer a core utilization from instruction count.

## Current branch limitations

- Generic sampling columns remain in the schema, but this branch's CLI exposes
  only the `hotspots` analysis mode. Their presence does not imply a supported
  sampling-mode run. `--simd-sampling` is a separate optional collection/analysis path.
- Legacy VF SIMT metadata, live-register and warp-occupancy analyzers are
  disabled pending helper-contract adaptation. Their tables/columns remain;
  show unavailable data as unavailable. Other optional analyzers can also be
  disabled or fail: inspect `AnalysisState` and actual data availability.
- Full source lines are available through `SourceLines` when
  the producer could read the file during report generation. Inline frame
  metadata remains limited to the existing instruction mappings.
- Aggregated views have no arbitrary runtime core filter and inherit the
  existing join/grouping semantics described above.

## `.npu-rep` consumption

The packer exports every non-empty table/view as `<SQLName>.csv`, includes an
export `manifest.json`, and copies generated report JSON files. Thus expect
`AsmMetrics.csv`, `SourceLineMetrics.csv`, source/debug tables, function/block
and hint data **when non-empty**. Empty tables/views have no corresponding CSV;
export failures are recorded in the manifest. Inspect the manifest and entries
instead of treating a missing file as a schema change or a zero-valued dataset.

CSV has no SQLite column types. Parse numeric fields deliberately, retain
missing-value semantics, and parse nested JSON fields separately. Preserve
64-bit composite IDs exactly (e.g. BigInt or strings in JavaScript); do not
round them through an unsafe Number conversion.

The archive contains exported rows. The producer reads the paths listed in
`SourceFiles` to populate `SourceLines`.
Source/Assembly HTML is absent by design in this branch. The
[Roofline CSV tables](roofline_data_format.md) describe a separate analysis contract.

## Reconstructing a source file

`SourceFiles.csv` maps `SourceFileId` to `SourceFilePath`. The path identifies
the file as it appeared on the machine that generated the report.

`SourceLines.csv` contains one row per source position. The columns are listed
in export order.

| Column | SQLite type | Meaning |
|---|---|---|
| `SourceFileId` | `INTEGER` | File ID from `SourceFiles.csv` |
| `SourceLineNum` | `INTEGER` | 1-based line number within that file |
| `SourceLineId` | `INTEGER` | Row ID referenced by `DebugInfo.SourceLineId` |
| `SourceLine` | `TEXT` | Line text, including leading and trailing whitespace |

Group rows by `SourceFileId` and sort each group numerically by
`SourceLineNum`. The pair is unique. Read the CSV with a CSV parser so quoted
text and empty line values are preserved. An empty string is a blank source
line. Rows sharing the same text at different positions have distinct
`SourceLineId` values.

```sql
SELECT SourceLineNum, SourceLine
FROM SourceLines
WHERE SourceFileId = :file_id
ORDER BY SourceLineNum;
```

Display source from these rows even when a position has no corresponding
`DebugInfo` row or runtime metrics. Use `DebugInfo` for instruction navigation
and attach source metrics by `(FileId, LineNum)`.

- Line text preserves comments, blank lines and indentation. Line terminators
  are omitted. Input uses UTF-8 with replacement for invalid bytes.
- The report adds lines without instruction mappings after analysis, including
  for paths added by optional analyzers. Keep sources matched to the compiled
  binary when generating the report.
- Missing, unreadable or non-regular files produce a warning and retain any
  known debug locations. Empty files contribute no line rows.
- To regenerate an older report, use the original recording before report
  analysis populated its source tables.

## Suggested UI loading sequence

1. Select the kernel/launch and inspect the archive/export manifest.
2. Index `SourceFiles` by file ID and build the source view from `SourceLines`.
   Use a host resolver when embedded source is unavailable.
3. Load `SourceInstructions` and `DebugInfo` to build the two correlation indexes
   with `(report, file, 1-based line)` keys.
4. Attach `AsmMetrics` by PC and `SourceLineMetrics` by `(FileId, LineNum)`.
5. Add optional hints, function/block decorations, IPC and summary charts.
6. Keep code navigation usable when optional metrics, source text or stacks
   are unavailable. Show a clear missing-source state for a known debug path.
