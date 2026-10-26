# Memory heatmap data format

`npusim` turns the raw memory accesses of a kernel into two tables that
describe a set of **memory views**. A view is one buffer instance laid over a
fixed grid of cells, with the traffic that landed in each cell aggregated. The
tables carry everything needed to draw a view, so a consumer never queries
`MemoryRWAccesses` itself and two consumers cannot disagree about the same
picture.

Produced by `Ascend950MemoryHeatmapAnalyzer`
(`npusim/prof/src/backend/analyzers/david/memory_heatmap_analyzer.py`), which
runs when `--memory-utilization` is passed.

Nothing here is stored that a consumer can work out for itself: the address of
a cell, the span of a view, how many cells it has, the busiest of them. They
are a line of arithmetic each, listed under *Deriving the rest*.

## Producing the data

```bash
CANNSIM_COLLECT=all BIPROF_OPTS=--memory-utilization npusim record <app> -s Ascend950 -g vf
# Or regenerate from an existing record:
BIPROF_OPTS=--memory-utilization npusim report -e <record-directory>
```

The run is deterministic: the same kernel database always yields the same rows.

It writes, under `REPORT_DIR`:

| Path | What it is |
|---|---|
| `results/<kernel>_reports/memory_heatmap_report.html` | tabbed heatmap |
| `results/<kernel>_reports/<db_stem>.npu-rep` | archive with CSV tables, manifest and report JSON |
| `MemoryHeatmapLayout.csv` inside the archive | one row per view |
| `MemoryHeatmapCells.csv` inside the archive | one row per touched cell |
| `manifest.json` inside the archive | every exported object, with its columns and row count |

The CSVs are written by `npusim/prof/tools/export_csv.py`, which exports every
table of the analysis database the same way:

- UTF-8, CRLF line endings, comma separated, quoted per RFC 4180 (Python's
  `csv.writer`);
- the first line is the column names, in the order given below;
- an empty field is SQL `NULL`; `BOOLEAN` is `0` or `1`;
- a table with no rows is **not written at all**, and appears in
  `manifest.json` with `"row_count": 0` and no `file` key.

The kernel database needs the memory accesses in it, which means the simulation
has to run with `CANNSIM_COLLECT` including `instr,mem` (or `all`).
Memory accesses need matching instructions to identify their owning core.
Without memory events the analyzer reports `MemoryRWAccesses holds no data`
and writes neither table. On CANN builds that ignore `CANNSIM_COLLECT`, use
CSL's patched helper or another working helper library.

## What a view is

A view is **one buffer instance**, not one memory type:

- the cube-side buffers -- L1, L0A, L0B, L0C -- exist once, so each is one
  view, whichever core made the access. A vector core writing into L1 is still
  writing the one L1.
- UB exists once per vector core, so it is one view per core: one owned by
  AIV0 and one by AIV1.

A view is identified by `(CoreId, CoreTypeId, MemoryType)`, the primary key of
both tables and the first three columns of each, in that order.

`MemoryRWAccesses` carries `CoreId 0` throughout and an `ExecInstrId` holding
only the low 32 bits of the instruction id, while `ExecutedInstructions` keeps
the core in the high bits of its own copy; masking both sides joins them and
gives the core that made each access. An access with no instruction at all is
dropped: those are whole-buffer events, not traffic.

A write the cube makes into UB -- how a fixpipe result gets there -- is reported
once per buffer it reaches, so those twin rows are collapsed into one access and
placed in **both** UB views.

## The grid

Every view is `Width` x `Height` cells, currently 32 x 64 = 2048, counted row by
row from the top. A cell is addressed by its row and column:

```
address of a cell = BaseAddress + (CellRow * Width + CellCol) * CellSize
address of a row  = BaseAddress + CellRow * Width * CellSize
```

`CellSize` is whatever divides the window into 2048 cells, so it differs per
view (128 B for a 256 KB UB, 32 B for a 64 KB L0A).

The window is normally the declared buffer: `BaseAddress` 0 and the view
covering `DeclaredSize`. When the accesses reach past the declared size the
window follows the addresses actually touched instead, and `OutsideDeclared` is
1 -- a view a consumer should mark, because its grid is not the buffer.

## What a cell counts

A cell counts **addresses**: the helper used to write one row of
`MemoryRWAccesses` per address, and an address is a 4-byte word, so a 32-byte
access is 8 addresses and a 512-byte one is 128. An access narrower than a word,
and one with no size at all, is still one address; rounding is per access. A
strided access touches single bytes a stride apart, so each byte it carries is
an address of its own.

Two things the raw table has are deliberately **not** counted here. Rows of
`MemoryRWAccesses` would count events, not volume -- one row carries anything
from a single byte to 16 KB, so a cell would be coloured by how the simulator
happened to batch the traffic. Bytes would miss that an access narrower than a
word still occupies one.

Per-cell counts are coverage: an access wider than one cell is counted in every
cell its bytes reach.

## MemoryHeatmapLayout

One row per view.

| Column | Type | Meaning |
|---|---|---|
| `CoreId` | INTEGER | As `MemoryRWAccesses` reports it; 0 for a single-chip run. |
| `CoreTypeId` | INTEGER | The core that owns the buffer, as `CoreTypes` numbers them -- that table carries its name (`AIC`, `AIV0`, `AIV1`). |
| `MemoryType` | INTEGER | The id the helper writes: 1 UB, 2 L0A, 3 L0B, 4 L0C, 5 L1. |
| `MemoryName` | VARCHAR(20) | The buffer alone: `UB`, `L1`, `L0A`, `L0B`, `L0C`, or `TYPE_<n>` for a memory type this build does not know. The database has no lookup for the helper's ids, so this is the one place they can be read by name; the owner comes from `CoreTypes`, so a view reads "AIV0 UB". |
| `Width`, `Height` | INTEGER | The grid, in cells: 32 and 64. |
| `CellSize` | INTEGER | Bytes one cell covers. |
| `BaseAddress` | INTEGER | Address the grid starts at. |
| `DeclaredSize` | INTEGER | The buffer's size per the architecture, or NULL if unknown. |
| `OutsideDeclared` | BOOLEAN | 1 when the accesses reach past `DeclaredSize`, so the grid follows what was touched instead of the buffer. |

## MemoryHeatmapCells

One row per **touched** cell. Untouched cells are absent -- a view of 2048 cells
with 901 touched has 901 rows -- so a consumer fills the grid with a default and
overlays these.

| Column | Type | Meaning |
|---|---|---|
| `CoreId`, `CoreTypeId`, `MemoryType` | INTEGER | The view this cell belongs to. |
| `CellRow`, `CellCol` | INTEGER | Position in the grid, from the top left. |
| `ReadAddresses`, `WriteAddresses` | INTEGER | Addresses of this cell that were read and written. |

## Deriving the rest

| Wanted | From |
|---|---|
| A cell's address | `BaseAddress + (CellRow * Width + CellCol) * CellSize` |
| A cell's id, if one is wanted | `CellRow * Width + CellCol` |
| What a cell is coloured by | `ReadAddresses + WriteAddresses` |
| The busiest cell of a view | the largest of those, over the view's rows |
| Cells touched | the number of rows the view has in `MemoryHeatmapCells` |
| The view's span | `CellSize * Width * Height` |
| Total / used / free | total = `DeclaredSize` (or the span when `OutsideDeclared`), used = cells touched x `CellSize`, free = the rest |
| The owner's name | `CoreTypes` on `CoreTypeId` |
| A view's full name | that name, then `MemoryName`: "AIV0 UB" |

## Worked example

`flash_attn_lite`, 1x1x128, on Ascend950. Six views:

| Owner | MemoryName | CellSize | DeclaredSize | Cells touched |
|---|---|---|---|---|
| AIV0 | `UB` | 128 B | 256 KB | 901 |
| AIV1 | `UB` | 128 B | 256 KB | 901 |
| AIC | `L1` | 256 B | 512 KB | 512 |
| AIC | `L0A` | 32 B | 64 KB | 2,048 |
| AIC | `L0B` | 32 B | 64 KB | 2,048 |
| AIC | `L0C` | 128 B | 256 KB | 1,024 |

The first cell of AIV0's UB holds 96 addresses, of L1 128, of L0A
16, of L0C 64 -- the same numbers the memory views produced when the
helper still wrote a row per address. Further along AIV0's UB, the cell at
`0x38100` holds 32 and the one at `0x38200` holds 64.

## Drawing a view

1. Read the view's row from `MemoryHeatmapLayout`.
2. Make a `Height` x `Width` grid, every cell "untouched".
3. For each of its rows in `MemoryHeatmapCells`, put
   `ReadAddresses + WriteAddresses` (or one of them, to show reads or writes
   alone) at `(CellRow, CellCol)`, keeping the largest as you go.
4. Colour a touched cell by `value / largest`; keep untouched cells a colour of
   their own, since "nothing happened here" is the first thing a reader looks
   for.
5. Label the rows with `BaseAddress + CellRow * Width * CellSize`. The columns
   carry no address of their own and are better left unlabelled.
6. Say what the buffer holds: total, used and free, from *Deriving the rest*.
7. Mark a view whose `OutsideDeclared` is 1: its grid is not the buffer.
