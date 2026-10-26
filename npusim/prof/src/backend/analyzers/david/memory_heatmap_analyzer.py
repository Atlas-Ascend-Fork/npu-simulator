# -*- coding: utf-8 -*-
# ----------------------------------------------------------------------------------------------------------
# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# This program is free software, you can redistribute it and/or modify it under the terms and conditions of
# CANN Open Software License Agreement Version 2.0 (the "License").
# Please refer to the License for details. You may not use this file except in compliance with the License.
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS FOR A PARTICULAR PURPOSE.
# See LICENSE in the root of the software repository for the full text of the License.
# ----------------------------------------------------------------------------------------------------------

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from npusim.prof.src.backend.analyzers.base.analyzer_base import IAnalyzer
from npusim.prof.src.backend.database import database_handler
from npusim.prof.src.common.biprof_constants import (
    MEM_UTIL_HEIGHT,
    MEM_UTIL_WIDTH,
    MAX_UINT_32,
    MEMORY_ACCESS_READ,
    memory_size_map,
)
from npusim.prof.src.common.biprof_context import biprof_ctx
from npusim.prof.src.common.biprof_enum import MemoryType
from npusim.prof.src.common.exceptions import RequirementsIncomplete


@dataclass
class CellStats:
    # What one grid cell holds, in addresses: 4-byte words, counted per access.
    read_addresses: int = 0
    write_addresses: int = 0

    def add(self, is_read: bool, addresses: int) -> None:
        if is_read:
            self.read_addresses += addresses
        else:
            self.write_addresses += addresses


@dataclass
class ViewExtent:
    # One view -- one buffer instance -- and the addresses it reaches over.
    # The cube-side buffers are one view each, UB is one per vector core.
    core_id: int
    mem_type: int
    owner_core: int
    name: str
    min_address: int = -1
    max_address: int = -1

    def add(self, addr: int, reach: int) -> None:
        """Widens the extent by one access, ``reach`` being its last byte."""
        if self.min_address < 0:
            self.min_address, self.max_address = addr, reach
            return
        self.min_address = min(self.min_address, addr)
        self.max_address = max(self.max_address, reach)


@dataclass(frozen=True)
class Access:
    # One row of MemoryRWAccesses, with the core that executed it resolved.
    core_id: int
    mem_type: int
    exec_core: int
    addr: int
    is_read: bool
    tick: int
    nbytes: int
    stride: int

    @property
    def reach(self) -> int:
        """The last byte the access touches, stride included."""
        return self.addr + (self.nbytes - 1) * self.stride

    @property
    def twin_key(self) -> Tuple:
        """What makes two rows the same access reported twice."""
        return (self.mem_type, self.addr, self.is_read, self.tick, self.nbytes)


@dataclass
class Window:
    # The address range one grid covers, and the size of a single cell.
    base: int
    span: int
    cell_size: int
    declared: Optional[int]
    outside_declared: bool


# What one address covers. The helper wrote a row of MemoryRWAccesses per
# address when it had no Size column, and those rows were 4-byte words.
ADDRESS_BYTES = 4


class Ascend950MemoryHeatmapAnalyzer(IAnalyzer):
    # Spatial view of MemoryRWAccesses: lays every memory type the kernel
    # touched over a fixed grid and aggregates the accesses landing in each
    # cell, so that drawing a memory view is just reading cells.
    #
    # This is deliberately an intermediate representation rather than a query
    # the frontend runs for itself. Choosing the address window is a decision
    # with logic behind it -- accesses can reach past the declared buffer size,
    # and then the grid has to follow the observed range instead of dropping
    # cells off the end -- and the access size matters, because one wide access
    # covers many cells. Keeping that in one place is what stops two consumers
    # from disagreeing about the same picture.
    #
    # Every memory type present in the data gets a view, including ones missing
    # from SUPPORTED_MEM_TYPES, such as OUT.

    WIDTH = MEM_UTIL_WIDTH
    HEIGHT = MEM_UTIL_HEIGHT
    CHUNK_SIZE = 50_000

    def __init__(self) -> None:
        super().__init__()
        # Resolved from the database in process(): which core type is the cube
        # and which are the vector cores.
        self._aic: int = 0
        self._vector_cores: Tuple[int, ...] = ()

    @staticmethod
    def get_name() -> str:
        return "ascend950_memory_heatmap"

    @property
    def total_cells(self) -> int:
        return self.WIDTH * self.HEIGHT

    def check_requirements(self) -> None:
        if not database_handler.table_has_data("MemoryRWAccesses"):
            raise RequirementsIncomplete("No data found in MemoryRWAccesses.")
        if not database_handler.table_has_data("ExecutedInstructions"):
            raise RequirementsIncomplete("No data found in ExecutedInstructions.")

    # -- reading the accesses ------------------------------------------------

    @staticmethod
    def _has_column(name: str) -> bool:
        """Whether MemoryRWAccesses carries this column in this database."""
        rows = database_handler.execute("PRAGMA table_info(MemoryRWAccesses)").fetchall()
        return any(row["name"] == name for row in rows)

    @classmethod
    def _size_expr(cls) -> str:
        """``Size`` as a byte count: an empty one means a single byte.

        Older kernel databases have no Size column at all, and even where it
        exists some rows leave it empty; either way the access is worth the one
        byte it names.
        """
        if cls._has_column("Size"):
            return "CASE WHEN Size IS NULL OR Size < 1 THEN 1 ELSE Size END"
        return "1"

    @classmethod
    def _stride_expr(cls) -> str:
        """``Step`` as a byte stride: an empty one, or 0, means no stride.

        An access of n bytes with stride s touches one byte every s, so it
        covers n bytes spread over n * s of the buffer. Without a stride the n
        bytes are contiguous, which is a stride of one.
        """
        if cls._has_column("Step"):
            return "CASE WHEN Step IS NULL OR Step < 1 THEN 1 ELSE Step END"
        return "1"

    def _core_types(self) -> Dict[str, int]:
        """Maps a core type name to its id, as the kernel database numbers them."""
        try:
            rows = database_handler.execute("SELECT CoreTypeId, CoreTypeName FROM CoreTypes").fetchall()
        except Exception:  # noqa: BLE001 - an older database has no CoreTypes
            return {}
        return {str(row["CoreTypeName"]).upper(): row["CoreTypeId"] for row in rows}

    def _owners(self, mem_type: int, exec_core: int) -> Tuple[int, ...]:
        """Which buffer instances one access belongs to.

        The cube-side buffers exist once, so an access belongs to the cube's
        copy whichever core made it -- a vector core writing into L1 is still
        writing the one L1. UB exists once per vector core, so a UB access
        belongs to the vector core that made it; one made by the cube, which is
        how a fixpipe result reaches UB, lands in both vector cores' buffers.
        """
        if mem_type != MemoryType.UB.value:
            return (self._aic,) if self._aic else (exec_core,)
        if exec_core in self._vector_cores:
            return (exec_core,)
        if self._vector_cores:
            return self._vector_cores
        return (exec_core,)

    @staticmethod
    def _buffer_name(mem_type: int) -> str:
        """What the buffer is called, without the core that owns it.

        The enum names the cube-side buffers with their owner (AIC_L0A), which
        the row's own CoreTypeId already says, so the prefix comes off here:
        a consumer joins CoreTypes when it wants the owner's name.
        """
        try:
            name = MemoryType(mem_type).name
        except ValueError:
            return f"TYPE_{mem_type}"
        _, _, buffer = name.partition("_")
        return buffer or name

    def _iter_accesses(self) -> Iterator[Access]:
        """Every access an instruction made, with its core resolved.

        MemoryRWAccesses carries CoreId 0 throughout and an ExecInstrId holding
        only the low 32 bits of the instruction id, while ExecutedInstructions
        keeps the core in the high bits of its own copy. Masking both sides
        joins them, and on flash_attn_lite that attributes every row that has
        an instruction: UB splits evenly between the two vector cores, and L1
        shows the cube plus each vector core writing its own half.

        Rows with no instruction are left out, as the memory views have always
        left them out. They are not traffic: on flash_attn_lite they are whole
        buffer events -- a read and a write of all 256 KB of L0C at one tick --
        which would otherwise stretch the L0C grid to twice the addresses the
        kernel really touched and colour every cell with a share of them.
        """
        size = self._size_expr()
        stride = self._stride_expr()
        # MemoryRWAccesses stores only the low 32 bits of ExecInstrId;
        # ExecutedInstructions carries the core in its high bits. Mask both to
        # match the access to its instruction and recover the owning core.
        rows = database_handler.iter_rows(
            f"""
            SELECT m.CoreId AS CoreId,
                   m.MemoryType AS MemoryType,
                   m.AccessedAddress AS Addr,
                   m.AccessMode AS Mode,
                   m.AccessTime AS Tick,
                   {size} AS Nbytes,
                   {stride} AS Stride,
                   e.CoreTypeId AS ExecCore
            FROM MemoryRWAccesses m
            LEFT JOIN ExecutedInstructions e
              ON (e.ExecInstrId & {MAX_UINT_32}) = (m.ExecInstrId & {MAX_UINT_32})
            WHERE m.ExecInstrId IS NOT NULL
            ORDER BY m.CoreId, m.MemoryType
            """
        )
        for row in rows:
            yield Access(
                core_id=row["CoreId"],
                mem_type=row["MemoryType"],
                exec_core=row["ExecCore"] or 0,
                addr=row["Addr"],
                is_read=row["Mode"] == MEMORY_ACCESS_READ,
                tick=row["Tick"],
                nbytes=row["Nbytes"],
                stride=row["Stride"],
            )

    def _iter_placed(self) -> Iterator[Tuple[Tuple[int, int, int], Access]]:
        """Every access paired with each view it belongs to.

        A fixpipe write into UB is reported once per buffer it reaches, so the
        twin rows are collapsed before being placed in both views -- otherwise
        each UB would be credited with the writes meant for the other.
        """
        pending: Optional[Access] = None
        for access in self._iter_accesses():
            owners = self._owners(access.mem_type, access.exec_core)
            if len(owners) > 1:
                if pending is not None and pending.twin_key == access.twin_key:
                    pending = None  # the twin: the pair is one access
                    continue
                pending = access
            for owner in owners:
                yield (access.core_id, owner, access.mem_type), access

    # -- laying out the grid -------------------------------------------------

    def _window(self, extent: ViewExtent) -> Window:
        """Chooses the address range the grid covers for one memory type."""
        declared: Optional[int] = None
        arch_sizes = memory_size_map.get(biprof_ctx.arch) or {}
        try:
            declared = arch_sizes.get(MemoryType(extent.mem_type))
        except ValueError:
            # A memory type this build of the tool knows nothing about.
            declared = None

        if declared is not None and extent.max_address < declared:
            # Show the whole buffer from zero, the way the memory views in the
            # source/assembly report have always been laid out.
            base, span, outside = 0, int(declared), False
        else:
            # The accesses reach past the declared size, or there is no declared
            # size to go by, so follow what was actually touched.
            base = extent.min_address
            span = extent.max_address - base + 1
            outside = declared is not None

        cell_size = max(1, -(-span // self.total_cells))
        return Window(
            base=base,
            span=cell_size * self.total_cells,
            cell_size=cell_size,
            declared=declared,
            outside_declared=outside,
        )

    def _cell_of(self, window: Window, addr: int) -> int:
        """The cell an address lands in, clamped to the grid."""
        cell_id = (addr - window.base) // window.cell_size
        return min(max(cell_id, 0), self.total_cells - 1)

    @staticmethod
    def _addresses(nbytes: int, stride: int) -> int:
        """How many addresses ``nbytes`` of an access occupy.

        The helper used to write one row of MemoryRWAccesses per address, and
        an address is a 4-byte word: a 32-byte access was eight rows, a 512-byte
        one a hundred and twenty-eight. An access narrower than a word, and one
        that carries no size at all, was still a row, so it is still one
        address. A strided access touches single bytes that are words apart, so
        each byte it carries is an address of its own.
        """
        if stride > 1:
            return nbytes
        return max(1, -(-nbytes // ADDRESS_BYTES))

    def _coverage(self, window: Window, addr: int, nbytes: int, stride: int) -> Dict[int, int]:
        """The addresses of one access that land in each cell.

        Contiguous bytes are measured by range, which is what nearly every
        access is. A strided access touches one byte every ``stride`` and leaves
        the bytes in between alone, so those are walked one at a time -- the
        cost is the bytes the access really carries, not the span it reaches
        over.
        """
        bytes_per_cell: Dict[int, int] = {}
        if stride > 1:
            for index in range(nbytes):
                cell_id = self._cell_of(window, addr + index * stride)
                bytes_per_cell[cell_id] = bytes_per_cell.get(cell_id, 0) + 1
        else:
            end = addr + nbytes - 1
            for cell_id in range(self._cell_of(window, addr), self._cell_of(window, end) + 1):
                cell_begin = window.base + cell_id * window.cell_size
                cell_end = cell_begin + window.cell_size - 1
                here = min(end, cell_end) - max(addr, cell_begin) + 1
                bytes_per_cell[cell_id] = max(here, 0)
        return {cell_id: self._addresses(here, stride) for cell_id, here in bytes_per_cell.items()}

    def _bin(self, cells: Dict[int, CellStats], window: Window, access: Access) -> None:
        """Records one access against every cell its addresses reach."""
        coverage = self._coverage(window, access.addr, access.nbytes, access.stride)
        for cell_id, addresses in coverage.items():
            cell = cells.get(cell_id)
            if cell is None:
                cell = CellStats()
                cells[cell_id] = cell
            cell.add(access.is_read, addresses)

    # -- writing the tables --------------------------------------------------

    def _layout_row(self, extent: ViewExtent, window: Window) -> Tuple:
        return (
            extent.core_id,
            extent.owner_core,
            extent.mem_type,
            extent.name,
            self.WIDTH,
            self.HEIGHT,
            window.cell_size,
            window.base,
            window.declared,
            window.outside_declared,
        )

    def _cell_rows(self, extent: ViewExtent, cells: Dict[int, CellStats]) -> List[Tuple]:
        return [
            (
                extent.core_id,
                extent.owner_core,
                extent.mem_type,
                cell_id // self.WIDTH,
                cell_id % self.WIDTH,
                cell.read_addresses,
                cell.write_addresses,
            )
            for cell_id, cell in sorted(cells.items())
        ]

    @staticmethod
    def _store_layouts(rows: List[Tuple]) -> None:
        database_handler.executemany(
            """
            INSERT INTO MemoryHeatmapLayout
            (CoreId, CoreTypeId, MemoryType, MemoryName,
             Width, Height, CellSize, BaseAddress,
             DeclaredSize, OutsideDeclared)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    def _store_cells(self, rows: List[Tuple]) -> None:
        # executemany_by_chunks opens its own transaction, which clashes with
        # the one already in flight by the time an analyzer runs.
        for start in range(0, len(rows), self.CHUNK_SIZE):
            database_handler.executemany(
                """
                INSERT INTO MemoryHeatmapCells
                (CoreId, CoreTypeId, MemoryType,
                 CellRow, CellCol, ReadAddresses, WriteAddresses)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows[start : start + self.CHUNK_SIZE],
            )

    @staticmethod
    def make_index() -> None:
        # MemoryRWAccesses stores only the low 32 bits of ExecInstrId; index
        # that same expression so the masked join in _iter_accesses can use it.
        database_handler.execute(
            f"CREATE INDEX IF NOT EXISTS idx_exec_instr_low32 ON ExecutedInstructions((ExecInstrId & {MAX_UINT_32}))"
        )

    @staticmethod
    def drop_index() -> None:
        database_handler.execute("DROP INDEX IF EXISTS idx_exec_instr_low32")

    def process(self) -> None:
        self.make_index()
        try:
            core_types = self._core_types()
            self._aic = core_types.get("AIC", 0)
            self._vector_cores = tuple(core_types[name] for name in ("AIV0", "AIV1") if name in core_types)

            extents: Dict[Tuple[int, int, int], ViewExtent] = {}
            for key, access in self._iter_placed():
                extent = extents.get(key)
                if extent is None:
                    core_id, owner, mem_type = key
                    extent = ViewExtent(
                        core_id=core_id,
                        mem_type=mem_type,
                        owner_core=owner,
                        name=self._buffer_name(mem_type),
                    )
                    extents[key] = extent
                extent.add(access.addr, access.reach)
            if not extents:
                return

            # The window comes from the extent, so the cells can only be binned
            # once every access of a view has been seen.
            windows = {key: self._window(extent) for key, extent in extents.items()}
            cells_map: Dict[Tuple[int, int, int], Dict[int, CellStats]] = {key: {} for key in extents}
            for key, access in self._iter_placed():
                self._bin(cells_map[key], windows[key], access)

            layout_rows: List[Tuple] = []
            cell_rows: List[Tuple] = []
            for key in sorted(extents):
                extent = extents[key]
                layout_rows.append(self._layout_row(extent, windows[key]))
                cell_rows.extend(self._cell_rows(extent, cells_map[key]))

            if layout_rows:
                self._store_layouts(layout_rows)
            if cell_rows:
                self._store_cells(cell_rows)
        finally:
            self.drop_index()
