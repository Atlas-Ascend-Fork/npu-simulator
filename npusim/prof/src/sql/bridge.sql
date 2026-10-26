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

-- bridge.sql: Creates tables and views that are NOT part of the contract DB
-- and NOT output tables (analysis.sql). Three categories:
--   1. Dictionary tables (populated by fill_static_tables)
--   2. ELF-derived tables (populated by ObjectProcessor when --object-file given)
--   3. Empty stubs for calog-only / sampling tables referenced by views
--   4. Aggregation views (moved from the former sim_db.sql)

PRAGMA synchronous = OFF;
PRAGMA page_size = 16384;

-- =============================================================================
-- 1. Dictionary tables
-- =============================================================================

CREATE TABLE IF NOT EXISTS InstrTypes(
    InstrTypeId INTEGER PRIMARY KEY,
    InstrTypeName VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS InstrQueueTypes(
    InstrQueueTypeId INTEGER PRIMARY KEY,
    InstrQueueTypeName VARCHAR(20) NOT NULL
);

CREATE TABLE IF NOT EXISTS CoreTypes(
    CoreTypeId INTEGER PRIMARY KEY,
    CoreTypeName VARCHAR(20) NOT NULL
);

-- =============================================================================
-- 2. ELF-derived tables (populated when --object-file is provided)
-- =============================================================================

CREATE TABLE IF NOT EXISTS Sections(
    SectionId INTEGER PRIMARY KEY AUTOINCREMENT,
    SectionName VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS SourceFiles(
    SourceFileId INTEGER PRIMARY KEY AUTOINCREMENT,
    SourceFilePath VARCHAR(200) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS SourceLines(
    SourceLineId INTEGER PRIMARY KEY AUTOINCREMENT,
    SourceLine VARCHAR(200) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS DebugInfo(
    DebugInfoId INTEGER PRIMARY KEY AUTOINCREMENT,
    SourceLineId INTEGER,
    SourceFileId INTEGER,
    DebugInfoLineNum INTEGER NOT NULL,
    FOREIGN KEY (SourceLineId) REFERENCES SourceLines (SourceLineId),
    FOREIGN KEY (SourceFileId) REFERENCES SourceFiles (SourceFileId)
);

CREATE TABLE IF NOT EXISTS SourceInstructions(
    SourceInstrSection INTEGER,
    SourceInstrAddr INTEGER,
    SourceInstrEncoding VARCHAR(24) NOT NULL,
    SourceInstrText VARCHAR(200) NOT NULL,
    DebugInfoId INTEGER,
    SourceInstrQueueType INTEGER,
    PRIMARY KEY (SourceInstrSection, SourceInstrAddr),
    FOREIGN KEY (SourceInstrSection) REFERENCES Sections (SectionId),
    FOREIGN KEY (DebugInfoId) REFERENCES DebugInfo (DebugInfoId),
    FOREIGN KEY (SourceInstrQueueType) REFERENCES InstrQueueTypes (InstrQueueTypeId)
);

CREATE TABLE IF NOT EXISTS BasicBlocks(
    SourceInstrAddr INTEGER NOT NULL,
    BBLength INTEGER NOT NULL,
    BBName VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS Functions(
    SourceInstrAddr INTEGER NOT NULL,
    FunctionSize INTEGER NOT NULL,
    FunctionName VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS CallStackIDs(
    SourceInstrAddr INTEGER PRIMARY KEY,
    CallStackId INTEGER NOT NULL,
    FOREIGN KEY (SourceInstrAddr) REFERENCES SourceInstructions (SourceInstrAddr)
);

-- =============================================================================
-- 3. Missing output DDL (was in sim_db.sql, not in analysis.sql)
-- =============================================================================

CREATE TABLE IF NOT EXISTS ArchDiagramMetrics(
    ArchDiagramId INTEGER PRIMARY KEY AUTOINCREMENT,
    ArchDiagramParameterName VARCHAR(20) NOT NULL,
    ArchDiagramParameterValue REAL NOT NULL
);

-- =============================================================================
-- 4. Empty stubs for calog-only tables (no contract equivalent)
--    Created so that aggregation views can be queried without error.
--    These remain empty when running on a contract DB.
-- =============================================================================

CREATE TABLE IF NOT EXISTS SIMTExecutionMasks(
    ExecInstrId INTEGER NOT NULL,
    PredicateMask INTEGER NOT NULL,
    ExecutionMask INTEGER NOT NULL,
    FullMask INTEGER NOT NULL,
    BranchEfficiency FLOAT NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS SIMTWarpOccupancy(
    ExecInstrId INTEGER NOT NULL,
    WarpOccupancy REAL NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS SIMTWarpUtilization(
    SIMTBlockId INTEGER NOT NULL,
    WarpId INTEGER NOT NULL,
    TickStart INTEGER NOT NULL,
    Duration INTEGER NOT NULL,
    WarpUtilization REAL NOT NULL,
    CoreId INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS DCacheBiuRequests(
    EventId INTEGER PRIMARY KEY AUTOINCREMENT,
    SourceInstrAddr INTEGER,
    RequestSize INTEGER NOT NULL,
    CacheLineAddress INTEGER NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    EventBeginTick INTEGER NOT NULL,
    EventEndTick INTEGER NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (SourceInstrAddr) REFERENCES SourceInstructions (SourceInstrAddr)
);

CREATE TABLE IF NOT EXISTS DCacheHitMissEvents(
    EventId INTEGER PRIMARY KEY AUTOINCREMENT,
    SourceInstrAddr INTEGER NOT NULL,
    CacheLineAddress INTEGER NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    IsHit BOOLEAN NOT NULL,
    EventTick INTEGER NOT NULL,
    FOREIGN KEY (CoreTypeId) REFERENCES CoreTypes (CoreTypeId),
    FOREIGN KEY (SourceInstrAddr) REFERENCES SourceInstructions (SourceInstrAddr)
);

-- Contract DB tables that may be absent in minimal DBs.
-- Referenced by SIMTGMPatterns view and vector_utilization_analyzer.

CREATE TABLE IF NOT EXISTS SIMTRegisterRead(
    ExecInstrId       INTEGER NOT NULL,
    RegIndex          INTEGER,
    Bias              INTEGER,
    ProcessedAddress  INTEGER,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS PredicateRegValues(
    ExecInstrId      INTEGER NOT NULL,
    PredRegIndex     INTEGER,
    PredRegLenBits   INTEGER,
    PredRegValue     VARCHAR(256),
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- =============================================================================
-- 5. Empty stubs for deleted sampling output tables
--    Referenced by AsmMetrics / SourceLineMetrics views.
-- =============================================================================

CREATE TABLE IF NOT EXISTS SamplingStats(
    ExecInstrId INTEGER NOT NULL,
    Active INTEGER NOT NULL,
    Stall INTEGER NOT NULL,
    EmptyTicks INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS CollectedSamples(
    ExecInstrId INTEGER NOT NULL,
    Tick INTEGER NOT NULL,
    Active INTEGER NOT NULL,
    Stall INTEGER NOT NULL,
    EmptyTicks INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS QueueFullStalls(
    ExecInstrId INTEGER NOT NULL,
    QueueFullStall INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS RegisterUsageStalls(
    ExecInstrId INTEGER NOT NULL,
    Memory INTEGER NOT NULL,
    Execution INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS SyncStalls(
    ExecInstrId INTEGER NOT NULL,
    SyncStall INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS InstrFetchStalls(
    ExecInstrId INTEGER NOT NULL,
    InstrFetchStall INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

CREATE TABLE IF NOT EXISTS PipeSamples(
    ExecInstrId INTEGER NOT NULL,
    PipeActive INTEGER NOT NULL,
    FOREIGN KEY (ExecInstrId) REFERENCES ExecutedInstructions (ExecInstrId)
);

-- =============================================================================
-- 6. Aggregation views (moved from the former sim_db.sql)
-- =============================================================================

DROP VIEW IF EXISTS AsmMetrics;
CREATE VIEW AsmMetrics AS
SELECT  SourceInstructions.SourceInstrAddr AS PC,
        SourceInstructions.SourceInstrText AS InstrName,
        CallStackId,
        DebugInfo.SourceFileId AS FileId,
        DebugInfo.SourceLineId AS LineId,
        DebugInfo.DebugInfoLineNum AS LineNum,
        InstrTypes.InstrTypeName AS Pipe,
        LN(0.5 * SUM(ExecInstrTickEnd - ExecInstrTickStart) + 1)
            / LN(0.5 * (SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr='instrs_time_sum') + 1) AS RelTime,
        COUNT(ExecutedInstructions.SourceInstrAddr) AS ExecutionCount,
        ExecutedInstructions.ExecInstrParams AS InstrParams,
        SUM(ExecInstrTickEnd - ExecInstrTickStart) AS TickCount,
        SUM(UBBankConflicts.ReadConflicts
            + UBBankConflicts.WriteConflicts) AS UBBankConflicts,
        SUM(UbBankConflictsCross.ConflictCount) AS UBCrossInstructionBankConflicts,
        AVG(VectorUtilizations.VectorUtilization) AS VectorUtilization,
        SUM(LiveRegisters.LiveGPRegisters) AS LiveGPRegisters,
        SUM(LiveRegisters.LiveSharedRegisters) AS LiveSharedRegisters,
        AVG(DmaMovSimpleParams.SrcStride) AS DmaSrcStride,
        AVG(DmaMovSimpleParams.DstStride) AS DmaDstStride,
        AVG(DmaMovSimpleParams.BurstNumber) AS BurstNumber,
        AVG(SIMTGmAccessMetrics.CacheLineCount) AS CacheLineCount,
        AVG(SIMTGmAccessMetrics.CacheLineUtilization) AS CacheLineUtilization,
        AVG(SIMTGmAccessMetrics.MemAccessStride) AS MemAccessStride,
        AVG(COALESCE(DmaMovProcessedBytes.ProcessedBytes,
                     VectorUtilizations.ProcessedBytes,
                     SIMTLdStProcessedBytes.ProcessedBytes)) AS ProcessedBytes,
        ROUND(AVG(DmaMovProcessedBytes.MemoryTraffic), 2) AS MemoryTraffic,
        VfSimtInvocations.ThreadDimX AS VfSimtTidX,
        VfSimtInvocations.ThreadDimY AS VfSimtTidY,
        VfSimtInvocations.ThreadDimZ AS VfSimtTidZ,
        VfSimtInvocations.RegisterNum AS VfSimtRegNum,
        AVG(SIMTExecutionMasks.BranchEfficiency) AS BranchEfficiency,
        AVG(SIMTWarpOccupancy.WarpOccupancy) AS WarpOccupancy,
        ROUND(AVG(IPCAsmMetrics.ScalarIPC), 2) AS ScalarIPC,
        ROUND(AVG(IPCAsmMetrics.ExecIPC), 2) AS ExecIPC,
        ROUND(AVG(IPCAsmMetrics.LdStIPC), 2) AS LdStIPC,
        ROUND(AVG(IPCAsmMetrics.BranchIPC), 2) AS BranchIPC,
        SUM(RegisterUsageStalls.Memory) AS MemoryStall,
        SUM(RegisterUsageStalls.Execution) AS ExecutionStall,
        SUM(QueueFullStalls.QueueFullStall) AS QueueFullStall,
        SUM(SamplingStats.Active) AS Active,
        SUM(SamplingStats.Stall) AS Stall,
        SUM(SamplingStats.EmptyTicks) AS EmptyTicks,
        SUM(SyncStalls.SyncStall) AS SyncStall,
        SUM(InstrFetchStalls.InstrFetchStall) AS InstrFetchStall,
        SUM(PipeSamples.PipeActive) AS PipeActive,
        SIMDStallsByAddr.StallsJson AS SIMDStallsJson
FROM SourceInstructions
LEFT JOIN ExecutedInstructions
ON        SourceInstructions.SourceInstrAddr = ExecutedInstructions.SourceInstrAddr
LEFT JOIN DebugInfo
ON        SourceInstructions.DebugInfoId=DebugInfo.DebugInfoId
LEFT JOIN InstrTypes
ON        InstrTypes.InstrTypeId=ExecutedInstructions.InstrTypeId
LEFT JOIN UnitsUsageMetrics
ON        SourceInstructions.SourceInstrAddr = UnitsUsageMetrics.SourceInstrAddr
LEFT JOIN UBBankConflicts
ON        UBBankConflicts.ExecInstrId = ExecutedInstructions.ExecInstrId
LEFT JOIN UbBankConflictsCross
ON        UbBankConflictsCross.ExecInstrId = ExecutedInstructions.ExecInstrId
LEFT JOIN VectorUtilizations
ON        VectorUtilizations.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN DmaMovProcessedBytes
ON DmaMovProcessedBytes.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN DmaMovSimpleParams
ON DmaMovSimpleParams.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN LiveRegisters
ON        LiveRegisters.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN SIMTGmAccessMetrics
ON        SIMTGmAccessMetrics.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN SIMTLdStProcessedBytes
ON        SIMTLdStProcessedBytes.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN VfSimtInvocations
       ON ExecutedInstructions.SourceInstrAddr=VfSimtInvocations.VfSimtAddr
LEFT JOIN SIMTExecutionMasks
       ON ExecutedInstructions.ExecInstrId=SIMTExecutionMasks.ExecInstrId
       AND SIMTExecutionMasks.FullMask > 0
LEFT JOIN SIMTWarpOccupancy
       ON ExecutedInstructions.ExecInstrId=SIMTWarpOccupancy.ExecInstrId
LEFT JOIN IPCAsmMetrics
       ON ExecutedInstructions.ExecInstrId = IPCAsmMetrics.ExecInstrId
LEFT JOIN RegisterUsageStalls
       ON ExecutedInstructions.ExecInstrId = RegisterUsageStalls.ExecInstrId
LEFT JOIN SamplingStats
       ON ExecutedInstructions.ExecInstrId = SamplingStats.ExecInstrId
LEFT JOIN SyncStalls
ON        ExecutedInstructions.ExecInstrId = SyncStalls.ExecInstrId
LEFT JOIN QueueFullStalls
ON        ExecutedInstructions.ExecInstrId = QueueFullStalls.ExecInstrId
LEFT JOIN InstrFetchStalls
ON        ExecutedInstructions.ExecInstrId = InstrFetchStalls.ExecInstrId
LEFT JOIN PipeSamples
ON        ExecutedInstructions.ExecInstrId = PipeSamples.ExecInstrId
LEFT JOIN CallStackIDs
ON        CallStackIDs.SourceInstrAddr = SourceInstructions.SourceInstrAddr
LEFT JOIN SIMDStallsByAddr
ON        SIMDStallsByAddr.SourceInstrAddr = SourceInstructions.SourceInstrAddr
GROUP BY SourceInstructions.SourceInstrAddr;

DROP VIEW IF EXISTS SourceLineMetrics;
CREATE VIEW SourceLineMetrics AS
SELECT  DebugInfo.DebugInfoId AS DebugInfoId,
        DebugInfo.SourceFileId AS FileId,
        DebugInfo.SourceLineId AS LineId,
        DebugInfo.DebugInfoLineNum AS LineNum,
        COUNT(ExecutedInstructions.SourceInstrAddr) AS ExecutionCount,
        SUM(ExecInstrTickEnd - ExecInstrTickStart) AS TickCount,
        MIN((
            LN(0.5 * SUM(ExecInstrTickEnd - ExecInstrTickStart) + 1)
                / LN(0.5 * (SELECT KernelInfoVal FROM KernelInfo WHERE KernelInfoAttr='instrs_time_sum') + 1)
            ),
            1.0
        ) AS RelTime,
        SUM(UBBankConflicts.ReadConflicts
            + UBBankConflicts.WriteConflicts) AS UBBankConflicts,
        SUM(UbBankConflictsCross.ConflictCount) AS UBCrossInstructionBankConflicts,
        AVG(VectorUtilizations.VectorUtilization) AS VectorUtilization,
        AVG(DmaMovSimpleParams.SrcStride) AS DmaSrcStride,
        AVG(DmaMovSimpleParams.DstStride) AS DmaDstStride,
        AVG(DmaMovSimpleParams.BurstNumber) AS BurstNumber,
        AVG(SIMTGmAccessMetrics.CacheLineCount) AS CacheLineCount,
        AVG(SIMTGmAccessMetrics.CacheLineUtilization) AS CacheLineUtilization,
        AVG(SIMTGmAccessMetrics.MemAccessStride) AS MemAccessStride,
        MAX(LiveRegisters.LiveGPRegisters) AS LiveGPRegisters,
        MAX(LiveRegisters.LiveSharedRegisters) AS LiveSharedRegisters,
        AVG(COALESCE(DmaMovProcessedBytes.ProcessedBytes,
                     VectorUtilizations.ProcessedBytes,
                     SIMTLdStProcessedBytes.ProcessedBytes)) AS ProcessedBytes,
        ROUND(AVG(DmaMovProcessedBytes.MemoryTraffic), 2) AS MemoryTraffic,
        AVG(SIMTExecutionMasks.BranchEfficiency) AS BranchEfficiency,
        AVG(SIMTWarpOccupancy.WarpOccupancy) AS WarpOccupancy,
        SUM(SamplingStats.Active) AS Active,
        SUM(SamplingStats.Stall) AS Stall,
        SUM(SamplingStats.EmptyTicks) AS EmptyTicks,
        SUM(RegisterUsageStalls.Memory) As MemoryStall,
        SUM(RegisterUsageStalls.Execution) As ExecutionStall,
        SUM(QueueFullStalls.QueueFullStall) AS QueueFullStall,
        SUM(SyncStalls.SyncStall) As SyncStall,
        SUM(InstrFetchStalls.InstrFetchStall) As InstrFetchStall,
        SUM(PipeSamples.PipeActive) AS PipeActive,
        SIMDStallsByLine.StallsJson AS SIMDStallsJson
FROM SourceInstructions
INNER JOIN ExecutedInstructions ON
           ExecutedInstructions.SourceInstrAddr=SourceInstructions.SourceInstrAddr
LEFT JOIN DebugInfo
ON        SourceInstructions.DebugInfoId=DebugInfo.DebugInfoId
LEFT JOIN SourceFiles
ON        DebugInfo.SourceFileId=SourceFiles.SourceFileId
LEFT JOIN UnitsUsageMetrics
ON        SourceInstructions.SourceInstrAddr = UnitsUsageMetrics.SourceInstrAddr
LEFT JOIN UBBankConflicts
ON        UBBankConflicts.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN UbBankConflictsCross
ON        UbBankConflictsCross.ExecInstrId = ExecutedInstructions.ExecInstrId
LEFT JOIN VectorUtilizations
ON        VectorUtilizations.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN DmaMovProcessedBytes
ON DmaMovProcessedBytes.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN DmaMovSimpleParams
ON DmaMovSimpleParams.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN SIMTGmAccessMetrics
ON        SIMTGmAccessMetrics.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN SIMTLdStProcessedBytes
ON        SIMTLdStProcessedBytes.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN LiveRegisters
ON        LiveRegisters.ExecInstrId=ExecutedInstructions.ExecInstrId
LEFT JOIN SIMTExecutionMasks
ON        ExecutedInstructions.ExecInstrId=SIMTExecutionMasks.ExecInstrId
AND       SIMTExecutionMasks.FullMask > 0
LEFT JOIN SIMTWarpOccupancy
ON        ExecutedInstructions.ExecInstrId=SIMTWarpOccupancy.ExecInstrId
LEFT JOIN RegisterUsageStalls
ON        ExecutedInstructions.ExecInstrId = RegisterUsageStalls.ExecInstrId
LEFT JOIN SamplingStats
ON        ExecutedInstructions.ExecInstrId = SamplingStats.ExecInstrId
LEFT JOIN SyncStalls
ON        ExecutedInstructions.ExecInstrId = SyncStalls.ExecInstrId
LEFT JOIN QueueFullStalls
ON        ExecutedInstructions.ExecInstrId = QueueFullStalls.ExecInstrId
LEFT JOIN InstrFetchStalls
ON        ExecutedInstructions.ExecInstrId = InstrFetchStalls.ExecInstrId
LEFT JOIN PipeSamples
ON        ExecutedInstructions.ExecInstrId = PipeSamples.ExecInstrId
LEFT JOIN SIMDStallsByLine
ON        SIMDStallsByLine.SourceFileId = DebugInfo.SourceFileId
AND       SIMDStallsByLine.DebugInfoLineNum = DebugInfo.DebugInfoLineNum
GROUP BY  DebugInfo.SourceFileId, DebugInfo.DebugInfoLineNum;

DROP VIEW IF EXISTS InstrTypeHistCount;
CREATE VIEW InstrTypeHistCount AS
SELECT      InstrTypes.InstrTypeName AS Type,
            COUNT(ExecutedInstructions.ExecInstrId) AS Count
FROM        ExecutedInstructions
INNER JOIN  InstrTypes
ON          ExecutedInstructions.InstrTypeId=InstrTypes.InstrTypeId
GROUP BY    InstrTypes.InstrTypeName;

DROP VIEW IF EXISTS InstrTypeHistClocks;
CREATE VIEW InstrTypeHistClocks AS
SELECT      InstrTypes.InstrTypeName AS Type,
	        SUM(ExecInstrTickEnd - ExecInstrTickStart) AS TickCount
FROM ExecutedInstructions
INNER JOIN  InstrTypes
ON          ExecutedInstructions.InstrTypeId=InstrTypes.InstrTypeId
GROUP BY    InstrTypes.InstrTypeName;

DROP VIEW IF EXISTS InstrNameHistCount;
CREATE VIEW InstrNameHistCount AS
SELECT      ExecutedInstructions.ExecInstrName AS Name,
            COUNT(ExecutedInstructions.ExecInstrId) AS Count
FROM        ExecutedInstructions
GROUP BY    ExecutedInstructions.ExecInstrName;

DROP VIEW IF EXISTS InstrNameHistClocks;
CREATE VIEW InstrNameHistClocks AS
SELECT      ExecutedInstructions.ExecInstrName AS Name,
            SUM(ExecInstrTickEnd - ExecInstrTickStart) AS TickCount
FROM        ExecutedInstructions
GROUP BY    ExecutedInstructions.ExecInstrName;

DROP VIEW IF EXISTS PipeUtilizationHist;
CREATE VIEW PipeUtilizationHist AS
SELECT InstrQueueTypes.InstrQueueTypeName AS PipeName,
       CoreTypes.CoreTypeName AS CoreName,
       AVG(PipeUtilization) AS Utilization
FROM PipesUtilization
INNER JOIN InstrQueueTypes
ON PipesUtilization.InstrQueueTypeId=InstrQueueTypes.InstrQueueTypeId
INNER JOIN CoreTypes
ON CoreTypes.CoreTypeId=PipesUtilization.CoreTypeId
GROUP BY InstrQueueTypes.InstrQueueTypeName, CoreTypes.CoreTypeId
ORDER BY PipesUtilization.InstrQueueTypeId;

DROP VIEW IF EXISTS AiCoreOccupancy;
CREATE VIEW AiCoreOccupancy AS
SELECT ExecInstrCoreId AS CoreId,
       CoreTypeId,
       MIN(ExecInstrTickStart) AS TickStart,
       MAX(ExecInstrTickEnd) AS TickEnd
FROM ExecutedInstructions
GROUP BY CoreId, CoreTypeId;

DROP VIEW IF EXISTS ActiveInstrTypes;
CREATE VIEW ActiveInstrTypes AS
SELECT      InstrTypes.InstrTypeName AS Name
FROM        ExecutedInstructions
INNER JOIN  InstrTypes
ON          ExecutedInstructions.InstrTypeId=InstrTypes.InstrTypeId
GROUP BY    InstrTypes.InstrTypeName;

DROP VIEW IF EXISTS BasicBlockCountDiagram;
CREATE VIEW BasicBlockCountDiagram AS
SELECT      BasicBlocks.BBName AS Name,
            COALESCE(AsmMetrics.ExecutionCount, 0) AS ExeCount
FROM        BasicBlocks
LEFT JOIN AsmMetrics
ON AsmMetrics.PC=BasicBlocks.SourceInstrAddr
GROUP BY BasicBlocks.BBName;

DROP VIEW IF EXISTS SharedPatterns;
CREATE VIEW SharedPatterns AS
SELECT  ExecutedInstructions.ExecInstrId AS ExecInstrId,
        ExecutedInstructions.ExecInstrName AS InstrName,
        SgUbRwAcesses.AccessedAddress AS AccessedAddress
FROM SgUbRwAcesses
INNER JOIN ExecutedInstructions
     ON ExecutedInstructions.ExecInstrId = SgUbRwAcesses.ExecInstrId;

DROP VIEW IF EXISTS SIMTGMPatterns;
CREATE VIEW SIMTGMPatterns AS
SELECT  ExecutedInstructions.ExecInstrId AS ExecInstrId,
        (SIMTRegisterRead.Bias / 4) AS ThreadId,
        SIMTRegisterRead.RegIndex AS RegIndex,
        SIMTRegisterRead.ProcessedAddress AS ProcessedAddress,
        ExecutionMasks.Mask AS Mask
FROM SIMTRegisterRead
LEFT JOIN ExecutedInstructions
     ON SIMTRegisterRead.ExecInstrId = ExecutedInstructions.ExecInstrId
INNER JOIN ExecutionMasks
    ON ExecutionMasks.ExecInstrId = ExecutedInstructions.ExecInstrId
WHERE ExecutedInstructions.ExecInstrName LIKE 'SIMT_LDG' OR ExecutedInstructions.ExecInstrName LIKE 'SIMT_STG';

DROP VIEW IF EXISTS SIMTSharedPatterns;
CREATE VIEW SIMTSharedPatterns AS
SELECT  ExecutedInstructions.ExecInstrId AS ExecInstrId,
        ExecutedInstructions.ExecInstrName AS InstrName,
        (SIMTRegisterRead.Bias / 4) AS ThreadId,
        ExecutionMasks.Mask AS Mask,
        SIMTRegisterRead.ProcessedAddress AS ProcessedAddress
FROM SIMTRegisterRead
LEFT JOIN ExecutedInstructions
     ON SIMTRegisterRead.ExecInstrId = ExecutedInstructions.ExecInstrId
INNER JOIN ExecutionMasks
    ON ExecutionMasks.ExecInstrId = ExecutedInstructions.ExecInstrId
WHERE ExecutedInstructions.ExecInstrName LIKE 'SIMT_LDS' OR ExecutedInstructions.ExecInstrName LIKE 'SIMT_STS';

-- =============================================================================
-- 7. VfPMU raw aggregation views (read from contract input tables)
-- =============================================================================

-- Cumulative counters of the last VF, per (core, coretype).
DROP VIEW IF EXISTS VfPMUViewPerSubcore;
CREATE VIEW VfPMUViewPerSubcore AS
SELECT
    V.CoreId,
    V.CoreTypeId,
    V.MetricId,
    M.MetricName,
    V.MetricValue
FROM VfPMUValues AS V
LEFT JOIN VfPMUMetrics AS M
    ON V.MetricId = M.MetricId
WHERE V.VfId = (
    SELECT MAX(V2.VfId)
    FROM VfPMUValues AS V2
    WHERE V2.CoreId = V.CoreId
      AND V2.CoreTypeId = V.CoreTypeId
);

-- Per-VF deltas of the cumulative raw counters.
-- First VF keeps its raw value (LAG defaults to 0 via COALESCE).
DROP VIEW IF EXISTS VfPMUDeltasViewPerVf;
CREATE VIEW VfPMUDeltasViewPerVf AS
SELECT
    V.CoreId,
    V.CoreTypeId,
    V.VfId,
    V.MetricId,
    M.MetricName,
    V.MetricValue - COALESCE(
        LAG(V.MetricValue) OVER (
            PARTITION BY V.CoreId, V.CoreTypeId, V.MetricId
            ORDER BY V.VfId
        ), 0
    ) AS MetricDelta
FROM VfPMUValues AS V
LEFT JOIN VfPMUMetrics AS M
    ON V.MetricId = M.MetricId;
