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

DROP TABLE IF EXISTS HintTypes;
DROP TABLE IF EXISTS HintMessages;
DROP TABLE IF EXISTS InstructionHints;
DROP TABLE IF EXISTS SourceLineHints;
DROP TABLE IF EXISTS KernelHints;
DROP VIEW IF EXISTS InstructionHintsView;
DROP VIEW IF EXISTS SourceLineHintsView;
DROP VIEW IF EXISTS KernelHintsView;

CREATE TABLE HintTypes(
    HintTypeId INTEGER PRIMARY KEY AUTOINCREMENT,
    HintTypeName VARCHAR(70) NOT NULL,
    HintPassed BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE HintMessages(
    HintMsgId INTEGER PRIMARY KEY AUTOINCREMENT,
    HintMsgText VARCHAR(500) NOT NULL
);

CREATE TABLE InstructionHints(
    HintId INTEGER PRIMARY KEY AUTOINCREMENT,
    HintTypeId INTEGER NOT NULL,
    HintMsgId INTEGER NOT NULL,
    PC INTEGER NOT NULL,
    FOREIGN KEY (HintTypeId) REFERENCES HintTypes (HintTypeId),
    FOREIGN KEY (HintMsgId) REFERENCES HintMessages (HintMsgId),
    FOREIGN KEY (PC) REFERENCES SourceInstructions (SourceInstrAddr)
);

CREATE TABLE SourceLineHints(
    HintId INTEGER PRIMARY KEY AUTOINCREMENT,
    HintTypeId INTEGER NOT NULL,
    HintMsgId INTEGER NOT NULL,
    SourceLineId INTEGER NOT NULL,
    FOREIGN KEY (HintTypeId) REFERENCES HintTypes (HintTypeId),
    FOREIGN KEY (HintMsgId) REFERENCES HintMessages (HintMsgId),
    FOREIGN KEY (SourceLineId) REFERENCES SourceLines (SourceLineId)
);

CREATE TABLE KernelHints(
    HintId INTEGER PRIMARY KEY AUTOINCREMENT,
    HintTypeId INTEGER NOT NULL,
    HintMsgId INTEGER NOT NULL,
    FOREIGN KEY (HintTypeId) REFERENCES HintTypes (HintTypeId),
    FOREIGN KEY (HintMsgId) REFERENCES HintMessages (HintMsgId)
);

CREATE VIEW InstructionHintsView AS
SELECT InstructionHints.PC AS PC,
       InstructionHints.HintTypeId AS HintTypeId,
       DebugInfo.SourceFileId AS FileId,
       DebugInfo.SourceLineId AS LineId,
       DebugInfo.DebugInfoLineNum AS LineNum,
       HintMessages.HintMsgText AS HintMsgText
FROM InstructionHints
LEFT JOIN SourceInstructions
ON        InstructionHints.PC = SourceInstructions.SourceInstrAddr
LEFT JOIN DebugInfo
ON        SourceInstructions.DebugInfoId = DebugInfo.DebugInfoId
LEFT JOIN HintMessages
ON        InstructionHints.HintMsgId = HintMessages.HintMsgId
GROUP BY HintMessages.HintMsgId;

CREATE VIEW SourceLineHintsView AS
SELECT DebugInfo.SourceFileId AS FileId,
       DebugInfo.DebugInfoLineNum AS LineNum,
       HintMessages.HintMsgText AS HintMsgText
FROM SourceLineHints
INNER JOIN DebugInfo
ON        SourceLineHints.SourceLineId = DebugInfo.SourceLineId
LEFT JOIN HintMessages
ON        SourceLineHints.HintMsgId = HintMessages.HintMsgId
GROUP BY HintMessages.HintMsgId;

CREATE VIEW KernelHintsView AS
SELECT KernelHints.HintTypeId AS HintTypeId,
       HintMessages.HintMsgText AS HintMsgText
FROM KernelHints
LEFT JOIN HintMessages
ON        KernelHints.HintMsgId = HintMessages.HintMsgId
GROUP BY HintMessages.HintMsgId;
