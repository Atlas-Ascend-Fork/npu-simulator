-- Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
--
-- Licensed under the Apache License, Version 2.0 (the "License");
-- you may not use this file except in compliance with the License.
-- You may obtain a copy of the License at
--
--     http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing, software
-- distributed under the License is distributed on an "AS IS" BASIS,
-- WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
-- See the License for the specific language governing permissions and
-- limitations under the License.

PRAGMA synchronous = OFF;

DROP TABLE IF EXISTS AnalysisState;
DROP TABLE IF EXISTS UnitsUsageMetrics;
DROP TABLE IF EXISTS DmaMovProcessedBytes;
DROP TABLE IF EXISTS DmaMovSimpleParams;
DROP TABLE IF EXISTS DmaMovMultiParams;
DROP TABLE IF EXISTS DmaMovLoad2dParams;
DROP TABLE IF EXISTS DmaMovLoad3dParams;
DROP TABLE IF EXISTS DmaMovNdDmaParams;
DROP TABLE IF EXISTS PipeDependency;
DROP TABLE IF EXISTS RegionNames;
DROP TABLE IF EXISTS Regions;
DROP TABLE IF EXISTS VectorUtilizations;
DROP TABLE IF EXISTS CallFunctions;
DROP TABLE IF EXISTS CallGraph;
DROP TABLE IF EXISTS CallStacks;
DROP TABLE IF EXISTS CallGraphMetrics;
DROP TABLE IF EXISTS UBBankConflicts;
DROP TABLE IF EXISTS MemoryUtilizationStates;
DROP TABLE IF EXISTS MemoryHeatmapLayout;
DROP TABLE IF EXISTS MemoryHeatmapCells;
DROP VIEW IF EXISTS MemoryUtilizationHitMapData;
DROP TABLE IF EXISTS LiveRegisters;
DROP TABLE IF EXISTS JumpsDescription;
DROP TABLE IF EXISTS VfSimtInvocations;
DROP TABLE IF EXISTS ExecutionMasks;
DROP TABLE IF EXISTS BasicBlocksRelations;
DROP TABLE IF EXISTS SIMTGmAccessMetrics;
DROP TABLE IF EXISTS VectorConfigState;
DROP TABLE IF EXISTS PipesUtilization;
DROP TABLE IF EXISTS MmadParams;
DROP TABLE IF EXISTS FixpParams;
DROP TABLE IF EXISTS SIMTLdStProcessedBytes;
DROP TABLE IF EXISTS UnitUtilization;
DROP TABLE IF EXISTS SubQueueTypes;
DROP TABLE IF EXISTS ScalarIpcDynamic;
DROP TABLE IF EXISTS VfIPC;
DROP TABLE IF EXISTS VfIPCInside;
DROP TABLE IF EXISTS VfSimtIPC;
DROP TABLE IF EXISTS VfSimtIPCInside;
DROP TABLE IF EXISTS VfIPCDynamic;
DROP VIEW IF EXISTS  VfIPCDynamicView;
DROP TABLE IF EXISTS VfSimtIPCDynamic;
DROP VIEW IF EXISTS  VfSimtIPCDynamicView;
DROP VIEW IF EXISTS  IPCAsmMetrics;
DROP TABLE IF EXISTS VFMemoryRWAccesses;
DROP TABLE IF EXISTS TraceBubbles;
DROP TABLE IF EXISTS TraceBubbleSummary;
DROP TABLE IF EXISTS IssueQueueUtilization;
DROP TABLE IF EXISTS ExecQueueUtilization;
DROP TABLE IF EXISTS SprInfoPerInstr;
DROP TABLE IF EXISTS CriticalPath;
DROP TABLE IF EXISTS SIMDStallsByAddr;
DROP TABLE IF EXISTS SIMDStallsByLine;
DROP TABLE IF EXISTS VfPMUSynthTypes;
DROP TABLE IF EXISTS VfPMUSynthValuesPerSubcore;
DROP TABLE IF EXISTS VfPMUSynthValuesPerVf;
DROP VIEW IF EXISTS VfPMUSynthViewPerSubcore;
DROP VIEW IF EXISTS VfPMUSynthViewPerVf;

CREATE TABLE AnalysisState(
    AnalysisName VARCHAR(70) NOT NULL,
    AnalysisPassed BOOLEAN NOT NULL
);

CREATE TABLE UnitsUsageMetrics(
    SourceInstrAddr INTEGER NOT NULL,
    UniqueSubCoresUsed INTEGER NOT NULL,
    UniqueWarpsUsed INTEGER,
    FOREIGN KEY (SourceInstrAddr) REFERENCES SourceInstructions (SourceInstrAddr)
);

CREATE TABLE DmaMovProcessedBytes(
    ExecInstrId INTEGER NOT NULL,
    ProcessedBytes INTEGER NOT NULL,
    MemoryTraffic REAL NOT NULL,
    CacheHit BOOLEAN NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE DmaMovSimpleParams(
    ExecInstrId INTEGER NOT NULL,
    BurstLength INTEGER NOT NULL,
    BurstNumber INTEGER NOT NULL,
    SrcStride INTEGER NOT NULL,
    DstStride INTEGER NOT NULL,
    SubBlockId INTEGER,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE DmaMovMultiParams(
    ExecInstrId INTEGER NOT NULL,
    N INTEGER NOT NULL,
    D INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE DmaMovLoad2dParams(
    ExecInstrId INTEGER NOT NULL,
    MStartPosition INTEGER NOT NULL,
    KStartPosition INTEGER NOT NULL,
    MStep INTEGER NOT NULL,
    KStep INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE DmaMovLoad3dParams(
    ExecInstrId INTEGER NOT NULL,
    MStep INTEGER NOT NULL,
    KStep INTEGER NOT NULL,
    Dst VARCHAR(100) NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE DmaMovNdDmaParams(
    ExecInstrId INTEGER NOT NULL,
    Loop0Size INTEGER NOT NULL,
    Loop1Size INTEGER NOT NULL,
    Loop2Size INTEGER NOT NULL,
    Loop3Size INTEGER NOT NULL,
    Loop4Size INTEGER NOT NULL,
    Loop0LpSize INTEGER NOT NULL,
    Loop0RpSize INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE PipeDependency(
    ExecInstrId INTEGER PRIMARY KEY NOT NULL,
    EventId INTEGER NOT NULL,
    EventName VARCHAR(50) NOT NULL,
    FlowEnd INTEGER NOT NULL,
    CategoryId INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE RegionNames(
    RegionNameId INTEGER PRIMARY KEY AUTOINCREMENT,
    RegionName VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE Regions(
    RegionNameId INTEGER NOT NULL,
    RegionStart INTEGER NOT NULL,
    RegionEnd INTEGER NOT NULL,
    RegionCoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    RegionTypeId INTEGER NOT NULL,
    InstrQueueTypeId INTEGER NOT NULL,
    WarpId INTEGER NOT NULL,
    FOREIGN KEY (RegionNameId) REFERENCES RegionNames (RegionNameId),
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (RegionTypeId) REFERENCES InstrQueueTypes (InstrQueueTypeId),
    FOREIGN KEY (InstrQueueTypeId) REFERENCES InstrQueueTypes (InstrQueueTypeId)
);

CREATE TABLE VectorUtilizations(
    ExecInstrId INTEGER NOT NULL,
    ProcessedBytes INTEGER NOT NULL,
    ProcessedElements INTEGER,
    VectorUtilization REAL NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE MemoryUtilizationStates(
    CoreId INTEGER NOT NULL,
    Tick INTEGER NOT NULL,
    UtilizedBytes INTEGER NOT NULL,
    MemoryType INTEGER NOT NULL,
    PRIMARY KEY (CoreId, Tick, MemoryType)
);

CREATE TABLE MemoryHeatmapLayout(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    MemoryType INTEGER NOT NULL,
    MemoryName VARCHAR(20) NOT NULL,
    Width INTEGER NOT NULL,
    Height INTEGER NOT NULL,
    CellSize INTEGER NOT NULL,
    BaseAddress INTEGER NOT NULL,
    DeclaredSize INTEGER,
    OutsideDeclared BOOLEAN NOT NULL,
    PRIMARY KEY (CoreId, CoreTypeId, MemoryType),
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId)
);

CREATE TABLE MemoryHeatmapCells(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    MemoryType INTEGER NOT NULL,
    CellRow INTEGER NOT NULL,
    CellCol INTEGER NOT NULL,
    ReadAddresses INTEGER NOT NULL,
    WriteAddresses INTEGER NOT NULL,
    PRIMARY KEY (CoreId, CoreTypeId, MemoryType, CellRow, CellCol),
    FOREIGN KEY (CoreId, CoreTypeId, MemoryType)
        REFERENCES MemoryHeatmapLayout (CoreId, CoreTypeId, MemoryType)
);

CREATE TABLE CallFunctions(
    CallFunctionId INTEGER PRIMARY KEY,
    SourceFileId INTEGER,
    FunctionName VARCHAR(128) NOT NULL,
    FOREIGN KEY(SourceFileId)
        REFERENCES SourceFiles (SourceFileId)
);

CREATE TABLE CallGraph(
    CallFunctionId INTEGER NOT NULL,
    ChildFunctionId INTEGER NOT NULL,
    CallSiteLine INTEGER,
    FOREIGN KEY(CallFunctionId)
        REFERENCES CallFunction (CallFunctionId),
    FOREIGN KEY(ChildFunctionId)
        REFERENCES CallFunction (CallFunctionId)
);

CREATE TABLE CallGraphMetrics(
    CallFunctionId INTEGER NOT NULL PRIMARY KEY,
    SelfExecutionCount INTEGER NOT NULL,
    SelfTickCount INTEGER NOT NULL,
    TotalExecutionCount INTEGER NOT NULL,
    TotalTickCount INTEGER NOT NULL,
    FOREIGN KEY(CallFunctionId)
        REFERENCES CallFunction (CallFunctionId)
);

CREATE TABLE CallStacks(
    CallFunctionId INTEGER NOT NULL,
    StackId INTEGER NOT NULL,
    StackLevel INTEGER NOT NULL,
    CallSiteLine INTEGER,
    FOREIGN KEY(CallFunctionId)
        REFERENCES CallFunctions (CallFunctionId)
);

CREATE TABLE UBBankConflicts(
    UBBankConflictId INTEGER PRIMARY KEY AUTOINCREMENT,
    ExecInstrId INTEGER NOT NULL,
    ReadConflicts INTEGER NOT NULL,
    WriteConflicts INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE LiveRegisters(
    LiveRegisterId INTEGER PRIMARY KEY AUTOINCREMENT,
    ExecInstrId INTEGER NOT NULL,
    LiveGPRegisters INTEGER NOT NULL,
    LiveSharedRegisters INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE JumpsDescription(
    Src INTEGER NOT NULL,
    Dst INTEGER NOT NULL
);

CREATE TABLE BasicBlocksRelations(
    StartBlockId INTEGER NOT NULL,
    EndBlockId INTEGER NOT NULL,
    FOREIGN KEY (StartBlockId) REFERENCES BasicBlocks (SourceInstrAddr),
    FOREIGN KEY (EndBlockId) REFERENCES BasicBlocks (SourceInstrAddr)
);

CREATE TABLE VfSimtInvocations(
    VfSimtAddr INTEGER PRIMARY KEY,
    KernelAddr INTEGER NOT NULL,
    ThreadDimX INTEGER NOT NULL,
    ThreadDimY INTEGER NOT NULL,
    ThreadDimZ INTEGER NOT NULL,
    RegisterNum INTEGER NOT NULL,
    CodeSizeBytes INTEGER NOT NULL
);

CREATE TABLE ExecutionMasks(
    ExecutionMaskId INTEGER PRIMARY KEY AUTOINCREMENT,
    ExecInstrId INTEGER NOT NULL,
    Mask INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE SIMTGmAccessMetrics(
    ExecInstrId INTEGER NOT NULL,
    CacheLineCount INTEGER NOT NULL,
    CacheLineUtilization REAL NOT NULL,
    MemAccessStride INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE VectorConfigState(
    VectorConfigStateId INTEGER PRIMARY KEY AUTOINCREMENT,
    ExecInstrId INTEGER NOT NULL,
    VectorConfigValue INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId)
        REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE PipesUtilization(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    InstrQueueTypeId INTEGER NOT NULL,
    PipeUtilization REAL NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (InstrQueueTypeId) REFERENCES InstrQueueTypes (InstrQueueTypeId)
);

CREATE TABLE MmadParams(
    ExecInstrId INTEGER NOT NULL,
    M INTEGER NOT NULL,
    K INTEGER NOT NULL,
    N INTEGER NOT NULL,
    MatrixADtype VARCHAR(16),
    MatrixBDtype VARCHAR(16),
    MatrixCDtype VARCHAR(16),
    EnabledMode VARCHAR(16),
    MatrixAL0AAddress INTEGER NOT NULL,
    MatrixBL0BAddress INTEGER NOT NULL,
    MatrixCAddress INTEGER NOT NULL,
    HwChecksUnitFlag BOOLEAN,
    HwSetsUnitFlag BOOLEAN,
    GEMVModeEnable BOOLEAN NOT NULL,
    MatrixCInBiasTableBuffer BOOLEAN NOT NULL,
    MatrixCInitVal BOOLEAN NOT NULL,
    MatrixAProcessedBytes INTEGER NOT NULL,
    MatrixBProcessedBytes INTEGER NOT NULL,
    MatrixCProcessedBytes INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE FixpParams(
    ExecInstrId INTEGER NOT NULL,
    N INTEGER NOT NULL,
    M INTEGER NOT NULL,
    L2CacheCtrlMode VARCHAR(50),
    HwChecksUnitFlag BOOLEAN NOT NULL,
    HwSetsUnitFlag BOOLEAN NOT NULL,
    PreQuantMode VARCHAR(100) NOT NULL,
    PreReluMode VARCHAR(20) NOT NULL,
    PreClipReluMode VARCHAR(30) NOT NULL,
    F32ChannelSplitEnable BOOLEAN NOT NULL,
    PostClipReluMode VARCHAR(30) NOT NULL,
    PostReluMode VARCHAR(20) NOT NULL,
    NZ2NDEnable BOOLEAN NOT NULL,
    NZ2DNEnable BOOLEAN NOT NULL,
    C0PaddingEnable BOOLEAN,
    DstMode VARCHAR(50),
    DstUBMask INTEGER,
    ProcessedBytes INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE SIMTLdStProcessedBytes(
    ExecInstrId INTEGER NOT NULL,
    ExecInstrName VARCHAR(100) NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    ProcessedBytes INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId),
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId)
);

CREATE TABLE UnitUtilization(
    Tick INTEGER NOT NULL,
    Util REAL NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId)
);

CREATE TABLE SubQueueTypes(
    ExecInstrId INTEGER NOT NULL,
    SubQueueType INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- IPCs for Scalar IPC Dynamic Histogram (and clear Scalar instructions) (1)
CREATE TABLE ScalarIpcDynamic(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    ExecInstrId INTEGER NOT NULL PRIMARY KEY,
    Tick INTEGER NOT NULL,
    IPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId),
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId)
);

-- IPCs of VF instructions (2.1)
CREATE TABLE VfIPC(
    ExecInstrId INTEGER NOT NULL,  -- of VF instruction
    ScalarIPC REAL NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- IPCs of instructions "inside" VFs (2.2)
CREATE TABLE VfIPCInside(
    ExecInstrId INTEGER NOT NULL PRIMARY KEY,  -- of some instruction "in" VF
    ScalarIPC REAL NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- IPCs of VF_SIMT instructions (3.1)
CREATE TABLE VfSimtIPC(
    ExecInstrId INTEGER NOT NULL,  -- of VF_SIMT instruction
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    BranchIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- IPCs of instructions "inside" VF_SIMTs (3.2)
CREATE TABLE VfSimtIPCInside(
    ExecInstrId INTEGER NOT NULL PRIMARY KEY,  -- of some instruction "in" VF_SIMT
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    BranchIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- (1) UNION (2.1) UNION (2.2) UNION (3.1) UNION (3.2)
CREATE VIEW IPCAsmMetrics AS
SELECT ExecInstrId, IPC AS ScalarIPC, 0.0 AS ExecIPC, 0.0 AS LdStIPC, 0.0 AS BranchIPC
FROM ScalarIpcDynamic
UNION ALL
SELECT ExecInstrId, ScalarIPC, ExecIPC, LdStIPC, 0.0 AS BranchIPC
FROM VfIPC
UNION ALL
SELECT ExecInstrId, ScalarIPC, ExecIPC, LdStIPC, 0.0 AS BranchIPC
FROM VfIPCInside
UNION ALL
SELECT ExecInstrId, 0.0, ExecIPC, LdStIPC, BranchIPC
FROM VfSimtIPC
UNION ALL
SELECT ExecInstrId, 0.0, ExecIPC, LdStIPC, BranchIPC
FROM VfSimtIPCInside;

-- IPCs for VF IPC Dynamic Histograms
CREATE TABLE VfIPCDynamic(
    ExecInstrId INTEGER NOT NULL UNIQUE,  -- of some instruction "in" VF
    WindowStartTick INTEGER NOT NULL,
    ScalarIPC REAL NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);
CREATE VIEW VfIPCDynamicView AS
SELECT
    ExecutedInstructions.ExecInstrCoreId AS CoreId,
    ExecutedInstructions.CoreTypeId AS CoreTypeId,
    VfIPCDynamic.WindowStartTick AS WindowStartTick,
    VfIPCDynamic.ScalarIPC AS ScalarIPC,
    VfIPCDynamic.ExecIPC AS ExecIPC,
    VfIPCDynamic.LdStIPC AS LdStIPC
FROM VfIPCDynamic
LEFT JOIN ExecutedInstructions
ON VfIPCDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
ORDER BY VfIPCDynamic.rowid;

-- IPCs for VF_SIMT IPC Dynamic Histograms
CREATE TABLE VfSimtIPCDynamic(
    ExecInstrId INTEGER NOT NULL UNIQUE,  -- of some instruction "in" VF_SIMT
    WindowStartTick INTEGER NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    BranchIPC REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);
CREATE VIEW VfSimtIPCDynamicView AS
SELECT
    ExecutedInstructions.ExecInstrCoreId AS CoreId,
    ExecutedInstructions.CoreTypeId AS CoreTypeId,
    VfSimtIPCDynamic.WindowStartTick AS WindowStartTick,
    VfSimtIPCDynamic.ExecIPC AS ExecIPC,
    VfSimtIPCDynamic.LdStIPC AS LdStIPC,
    VfSimtIPCDynamic.BranchIPC AS BranchIPC
FROM VfSimtIPCDynamic
LEFT JOIN ExecutedInstructions
ON VfSimtIPCDynamic.ExecInstrId=ExecutedInstructions.ExecInstrId
ORDER BY VfSimtIPCDynamic.rowid;

CREATE TABLE IssueQueueUtilization(
    Tick INTEGER NOT NULL,
    Util REAL NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    InstrTypeId INTEGER NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (InstrTypeId) REFERENCES InstrTypes (InstrTypeId)
);

CREATE TABLE ExecQueueUtilization(
    Tick INTEGER NOT NULL,
    Util REAL NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    InstrTypeId INTEGER NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (InstrTypeId) REFERENCES InstrTypes (InstrTypeId)
);

CREATE TABLE VFMemoryRWAccesses(
    AccessedAddress INTEGER NOT NULL,
    AccessMode VARCHAR(1) NOT NULL,
    AccessTime INTEGER NOT NULL,
    MemoryType INTEGER NOT NULL,
    ExecInstrId INTEGER,
    CoreId INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE SprInfoPerInstr(
    ExecInstrId INTEGER NOT NULL,
    SprName VARCHAR(20) NOT NULL,
    SprValue VARCHAR(20) NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE CriticalPath(
    EventId INTEGER PRIMARY KEY AUTOINCREMENT,
    InstrId INTEGER NOT NULL,
    FOREIGN KEY (InstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE SIMDStallsByAddr(
    SourceInstrAddr INTEGER NOT NULL,
    StallsJson TEXT NOT NULL,
    FOREIGN KEY (SourceInstrAddr) REFERENCES SourceInstructions (SourceInstrAddr)
);

CREATE TABLE SIMDStallsByLine(
    SourceFileId INTEGER,
    DebugInfoLineNum INTEGER,
    StallsJson TEXT NOT NULL
);

-- VfPMU synthetic (computed) counter types — dictionary of the top-down
-- hierarchy nodes populated by the VfPMUAnalyzer.
CREATE TABLE VfPMUSynthTypes(
    SynthId INTEGER PRIMARY KEY,
    SynthName VARCHAR(64) NOT NULL UNIQUE,
    SynthParentId INTEGER,
    SynthLevel INTEGER NOT NULL,
    SynthScope INTEGER NOT NULL DEFAULT 3,
    FOREIGN KEY (SynthParentId) REFERENCES VfPMUSynthTypes (SynthId)
);

CREATE TABLE VfPMUSynthValuesPerSubcore(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    SynthId INTEGER NOT NULL,
    SynthValue REAL NOT NULL,
    PRIMARY KEY (CoreId, CoreTypeId, SynthId),
    FOREIGN KEY (SynthId) REFERENCES VfPMUSynthTypes (SynthId)
);

CREATE TABLE VfPMUSynthValuesPerVf(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    VfId INTEGER NOT NULL,
    SynthId INTEGER NOT NULL,
    SynthValue REAL NOT NULL,
    PRIMARY KEY (CoreId, CoreTypeId, VfId, SynthId),
    FOREIGN KEY (SynthId) REFERENCES VfPMUSynthTypes (SynthId)
);

CREATE VIEW VfPMUSynthViewPerSubcore AS
SELECT
    S.CoreId,
    S.CoreTypeId,
    T.SynthId,
    T.SynthName,
    T.SynthParentId,
    P.SynthName AS SynthParentName,
    T.SynthLevel,
    S.SynthValue
FROM VfPMUSynthValuesPerSubcore AS S
LEFT JOIN VfPMUSynthTypes AS T
    ON S.SynthId = T.SynthId
LEFT JOIN VfPMUSynthTypes AS P
    ON T.SynthParentId = P.SynthId;

CREATE VIEW VfPMUSynthViewPerVf AS
SELECT
    S.CoreId,
    S.CoreTypeId,
    S.VfId,
    T.SynthId,
    T.SynthName,
    T.SynthParentId,
    P.SynthName AS SynthParentName,
    T.SynthLevel,
    S.SynthValue
FROM VfPMUSynthValuesPerVf AS S
LEFT JOIN VfPMUSynthTypes AS T
    ON S.SynthId = T.SynthId
LEFT JOIN VfPMUSynthTypes AS P
    ON T.SynthParentId = P.SynthId;

CREATE TABLE TraceBubbles(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    MainPipeline TEXT NOT NULL,
    SubType TEXT NOT NULL,
    Category TEXT NOT NULL,
    TickStart INTEGER NOT NULL,
    TickEnd INTEGER NOT NULL,
    GapTicks INTEGER NOT NULL,
    GapUs REAL NOT NULL,
    Reason TEXT,
    Optimizable INTEGER NOT NULL,
    OptimizationHint TEXT,
    WaitedPipeline TEXT,
    IterationIndex INTEGER
);

CREATE TABLE TraceBubbleSummary(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    MainPipeline TEXT NOT NULL,
    TotalDurationUs REAL NOT NULL,
    BubbleCount INTEGER NOT NULL,
    TopBottleneck TEXT,
    SubtypeSummary TEXT
);
