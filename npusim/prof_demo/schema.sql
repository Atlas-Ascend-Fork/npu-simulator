-- Derived data only; source DB is attached read-only as sim.
CREATE VIEW InstrTypeHistCount AS
SELECT EP.instr_type AS Type, COUNT(EI.ExecInstrId) AS Count
FROM ExecutedInstructionsAdjusted AS EI
JOIN ExtendParamsProjection AS EP ON EI.ExecInstrId = EP.ExecInstrId
GROUP BY EP.instr_type;

CREATE VIEW AiCoreOccupancy AS
SELECT ExecInstrCoreId AS CoreId, CoreTypeId,
       MIN(ExecInstrTickStart) AS TickStart, MAX(ExecInstrTickEnd) AS TickEnd
FROM ExecutedInstructionsAdjusted GROUP BY CoreId, CoreTypeId;

CREATE TABLE DmaMovProcessedBytes(
    ExecInstrId INTEGER NOT NULL,
    ProcessedBytes INTEGER NOT NULL,
    MemoryTraffic REAL NOT NULL

);

CREATE TABLE PipesUtilization(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    InstrQueueTypeId INTEGER NOT NULL,
    PipeUtilization REAL NOT NULL

);

CREATE TABLE VfIPC(
    ExecInstrId INTEGER NOT NULL,
    ScalarIPC REAL NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL

);

CREATE TABLE VfSimtIPC(
    ExecInstrId INTEGER NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL,
    BranchIPC REAL NOT NULL

);

CREATE TABLE PipeDependency(
    ExecInstrId INTEGER PRIMARY KEY NOT NULL,
    EventId INTEGER NOT NULL,
    EventName VARCHAR(50) NOT NULL,
    FlowEnd INTEGER NOT NULL,
    CategoryId INTEGER NOT NULL

);

CREATE TABLE UnitUtilization(
    Tick INTEGER NOT NULL,
    Util REAL NOT NULL,
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL
);

CREATE TABLE ScalarIpcDynamic(
    CoreId INTEGER NOT NULL,
    CoreTypeId INTEGER NOT NULL,
    ExecInstrId INTEGER NOT NULL PRIMARY KEY,
    Tick INTEGER NOT NULL,
    IPC REAL NOT NULL
);

CREATE TABLE VfIPCDynamic(
    ExecInstrId INTEGER NOT NULL UNIQUE,  -- of some instruction "in" VF
    WindowStartTick INTEGER NOT NULL,
    ScalarIPC REAL NOT NULL,
    ExecIPC REAL NOT NULL,
    LdStIPC REAL NOT NULL
);
