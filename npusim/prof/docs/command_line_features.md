# Report features and collection dependencies

`--feature-list` selects optional report analyses as a comma-separated list.
During recording, NPUSim derives `CANNSIM_COLLECT` from those features. The base
report requires `instr,ccu,simt,spr`; selected optional BiProf features add their
dependencies.
An explicitly set `CANNSIM_COLLECT` is preserved, including an empty value.

## Feature map

These dependencies describe the Ascend950 (`v100`) helper database contract.
`instr` includes instruction timing, PCs and `ExtendParams`; derived tables do
not require separate helper callbacks. Object-dependent outputs additionally
need a matching device ELF. Source correlation needs DWARF and accessible source
files. No collection category supplies missing debug information.

| Feature | Required `CANNSIM_COLLECT` | Input / derived data | Consumer / output |
| --- | --- | --- | --- |
| Base report (always enabled) | `instr,ccu,simt,spr` | Instructions/metadata, `DispatchTime`, `SIMTRegisterRead`, `SprWriteEvents`; matching device ELF/DWARF for source correlation | Base analyses, overall scalar IPC, queue/dispatch statistics, SIMT GM address metrics, SPR annotations and Source/Assembly tables |
| `scalar-ipc` | Covered by the base report | Instruction timing and dispatch times when available | Compatibility feature name for overall scalar IPC in `KernelInfo` |
| `vec-ipc` | `instr` | Timing and instruction groups within each vector-function invocation | Aggregate IPC in `VfIPC` and `VfSimtIPC` |
| `simd-perf` | `instr,pmu` | `VfPMUValues`, `VfPMUMetrics`, `VfPMUSummary` and their aggregation views | `VfPMUAnalyzer`; VF/subcore top-down metrics |
| `simd-sampling` | `instr,simd` | `SIMDSamplingStats`, `SIMDStallReasonTypes`, instruction IDs; object/DWARF for source labels | SIMD sampling stall statistics / timeline |
| `memory-utilization` | `instr,mem` | Local `MemoryRWAccesses`, joined to instructions | Memory utilization states and memory heatmap layout/cells |
| `roofline` | `instr,ub` | Instruction-derived compute/DMA/SIMT facts; `UbRwStatistics` for UB traffic; object function boundaries | `Roofline*` tables and `roofline.json` |
| `smart-pipes` | `instr,ccu` | Instruction types/synchronization and `DispatchTime` | Split compute/sync queues, queue utilization and dispatch presentation |
| `critical-path` | `instr,ccu` | Instructions + derived `PipeDependency`; `DispatchTime` for synchronization wait durations | `CriticalPath` and its trace |
| `bubble` | `instr,icache` | Instruction timing/parameters and cache-miss events from `ICacheEvents` | `TraceBubbles`, `TraceBubbleSummary` and bubble trace; ICache events support cache-miss classification |
| `vf-report-json` | `instr` | VF timing, instruction composition and VF IPC results | Structured VF JSON; also requests the `vec-ipc` calculation |

`simd-perf` uses **`pmu`**, while `simd-sampling` uses **`simd`**. These helper
category names do not match the feature names directly. `smart-pipes` and
`critical-path` derive dependency identities from instruction parameters. Their
full queue/wait presentation also needs `DispatchTime` from `ccu`; neither reads
`BrifEvents`.

Source/Assembly is part of the base report, not a separate selectable feature.
Its tables (`SourceInstructions`, `DebugInfo`, `AsmMetrics`, `SourceLineMetrics`)
are populated when a matching device object and the required debug/source data
are available. Selected optional analyses can add metrics to those views.

SIMT GM address analysis and SPR instruction annotations are existing base
analyses, without separate BiProf flags. Base collection includes `simt,spr`
for their input. Base `ccu` events also supply queue utilization, dispatch spans
and synchronization wait durations. They remain collected when critical-path
and smart-pipes are disabled. SIMT IPC, execution masks and the current Roofline calculation
use instruction data independently of the SIMT address stream.

`bubble` is an existing BiProf option and is enabled by default. Its cache-miss
classification reads `ICacheEvents`, so it adds the `icache` collection category.
There is no separate `--icache` option. Disabling `bubble` removes that automatic
collection dependency; existing ICache data remains available to the original
trace and summary consumers. `smart-pipes` is available only when selected
explicitly and is not part of the default feature list.

`memory-utilization` selects local-memory collection via `mem`.
`SgUbRwAcesses` is a separate address stream collected by `ub`, not `mem`.

## Other report data and all helper categories

The optional flags above do not describe every field that existing report code
can consume. The following map makes those extra inputs explicit, including collectors
outside the default feature set. Any of these categories can be requested
explicitly with `CANNSIM_COLLECT`.

| Category | Helper data | Existing consumer / consequence when omitted |
| --- | --- | --- |
| `instr` | `ExecutedInstructions`, kernel metadata | Required base input for report analysis |
| `mem` | Local `MemoryRWAccesses` | Local memory utilization and heatmap |
| `ub` | `UbRwStatistics`, `UbBankConflictsCross`, `SgUbRwAcesses` | Roofline UB traffic and UB portions of the architecture diagram; `SharedPatterns` -> shared bank conflicts, shared-memory and vector-fusion hints. Scatter/gather address data belongs to `ub`, not `mem` |
| `ccu` | `DispatchTime`, `QueueFullEvents`, `CCUAllTickEvents` | Dispatch spans, synchronization wait durations, issue/exec queue utilization, queue-full spans; aggregate scalar IPC also prefers dispatch times when present. CCU tick rows have no direct current consumer |
| `pmu` | `PMUScalarCounters`, `VfPMUMetrics`, `VfPMUSummary`, `VfPMUValues` | VF PMU top-down analysis |
| `simd` | `SIMDSamplingStats`, `SIMDStallReasonTypes` | SIMD sampling analysis |
| `simt` | `SIMTRegisterRead` | `SIMTGMPatterns` / `SIMTSharedPatterns`; per-thread global-memory addresses and SIMT coalescing metrics. Required in addition to instruction masks for this address-based analysis |
| `spr` | `SprWriteEvents` | `SPRInstrInfoAnalyzer` -> `SprInfoPerInstr`, enriching instruction trace arguments. Absent SPR events leave those details empty |
| `pred` | `PredicateRegValues` | No direct consumer in the current profiler. `ExecutionMasks` and branch efficiency read `exec_mask` / `prdct_mask` from instruction `ExtendParams`, not this table |
| `brif` | `BrifEvents` | Explicit collection only. No direct current consumer of `BrifEvents`; instruction-based branch efficiency does not read that table |
| `icache` | `ICacheEvents`, `ICacheRefillEvents`, `ICacheStartingPCs` | ICache trace events and `summary.json` refill metrics. Without it no ICache events are shown; an empty refill count must not be interpreted as evidence of zero cache misses |
| `all` | All callback categories | Explicit opt-in only; NPUSim never generates this value automatically |

An explicit `CANNSIM_COLLECT` remains authoritative, even if it omits data
needed by base or selected analyses. A successful analyzer or an existing table
does not prove that the required events were collected.

The existing call-stack and matrix-copy flags remain accepted through
`BIPROF_OPTS` for compatibility, but are not feature-list entries or defaults.
Legacy VF SIMT metadata, live-register and warp-occupancy analyzers remain
disabled pending helper-contract adaptation. SIMT address reads are not a
live-register/occupancy analysis.

`critical-path` is not enabled by default while its implementation is pending.
`vf-report-json` is also explicit opt-in. Use `--feature-list` or `BIPROF_OPTS`
to request them; `record -g vf` remains an explicit VF JSON export request.
Ordinary `record -g` and `report` do not export VF JSON by default.

## Defaults and selection

The single-chip default is:

```text
scalar-ipc,vec-ipc,simd-perf,simd-sampling,memory-utilization,roofline,bubble
```

Its collection union is `instr,mem,ub,ccu,pmu,simd,simt,spr,icache`, not `all`.
For multi-chip reports, the implicit defaults omit `scalar-ipc` and `vec-ipc`,
preserving the existing multi-chip policy. Explicit selections
remain authoritative.

Examples (no CSL or explicit object argument required):

```bash
# Default feature list; print the resolved categories before execution.
npusim record ./app -s Ascend950 -n 0 -g

# Only these optional analyses plus base data; collect instr,ub,ccu,simt,spr.
npusim record ./app -s Ascend950 -n 0 -g --feature-list scalar-ipc,roofline

# Base report, including available Source/Assembly; collect instr,ccu,simt,spr.
npusim record ./app -s Ascend950 -n 0 -g --feature-list ''

# Record inputs now for a later Roofline report, without generating one now.
npusim record ./app -s Ascend950 --feature-list roofline
npusim report -e /path/to/recording -n 0 --feature-list roofline
```

A list replaces the optional defaults. Names are stripped of surrounding spaces,
duplicates are removed, and unknown names are rejected before running the app.
`-g` still controls whether recording generates a report. `-g vf` and the legacy
`--smart-pipes` option remain explicit additional requests.

Priority:

1. Explicit `CANNSIM_COLLECT` always wins for collection, including `all` or an
   empty value. NPUSim does not add missing categories to it.
2. Explicit `--feature-list` selects the feature set. Other `BIPROF_OPTS`
   settings, such as log level or object path, remain usable; environment feature
   flags do not add unwanted analyses to this list.
3. Without a CLI feature list, an explicitly set `BIPROF_OPTS` replaces the
   defaults, including `BIPROF_OPTS=''`. Collection follows its feature flags.
4. Otherwise, use the default feature list and its collection union.

For example, `BIPROF_OPTS='--roofline'` still selects only Roofline plus base
analysis, and automatically collects `instr,ub,ccu,simt,spr` unless collection is overridden.
Changing features at **report** time cannot collect missing data from an old
recording. A selected analysis can legitimately lack events for a particular
kernel: pure SIMT kernels need not produce SIMD PMU, SIMD sampling or local
memory-access events.

## How to audit the map

The dependency mapping and the separate default feature tuple used by record, ktest and report are
[`core/public/features.py`](../../core/public/features.py).
Feature-to-analyzer selection lives in
[`analyzer_manager.py`](../src/backend/analyzer_manager.py).
Raw-to-derived relationships live in [`bridge.sql`](../src/sql/bridge.sql).
The consumers above are in [`backend/analyzers`](../src/backend/analyzers),
[`chrome_tracing_default.py`](../src/frontend/tracing/chrome_tracing_default.py)
and [`summary_reporter.py`](../src/frontend/summary_reporter.py).

The helper is supplied by CANN. Category availability and callback payloads are
properties of that helper build. Verify collection using actual helper masks
and input/derived table contents, not just a report's success message.
