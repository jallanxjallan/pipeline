PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    executor    TEXT NOT NULL,
    entrypoint  TEXT
);

CREATE TABLE IF NOT EXISTS instructions (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    body        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_steps (
    plan_id     TEXT NOT NULL
                REFERENCES plans(id) ON DELETE CASCADE,
    step_id     TEXT NOT NULL
                REFERENCES steps(id) ON DELETE RESTRICT,
    position    INTEGER NOT NULL CHECK (position > 0),

    PRIMARY KEY (plan_id, step_id),
    UNIQUE (plan_id, position)
);

CREATE TABLE IF NOT EXISTS step_instructions (
    step_id        TEXT NOT NULL
                   REFERENCES steps(id) ON DELETE CASCADE,
    instruction_id TEXT NOT NULL
                   REFERENCES instructions(id) ON DELETE RESTRICT,
    position       INTEGER NOT NULL CHECK (position > 0),

    PRIMARY KEY (step_id, instruction_id),
    UNIQUE (step_id, position)
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_step
    ON plan_steps(step_id);

CREATE INDEX IF NOT EXISTS idx_step_instructions_instruction
    ON step_instructions(instruction_id);
