-- ===========================================================================
-- 11_tasks.sql   ·   the task DAG
--
-- Dynamic Tables carry everything they can: they are declarative, they have a
-- declared TARGET_LAG, and there is no cron anywhere in the feature, state,
-- transition or baseline layers.
--
-- Tasks exist only for the four things a Dynamic Table cannot do:
--
--   1. MATCH_RECOGNIZE. A dynamic table containing it will not refresh
--      incrementally: explicit REFRESH_MODE=INCREMENTAL fails compilation with
--      an unsupported construct, and AUTO silently resolves to FULL. Rather
--      than accept a silent full rebuild on a one-minute lag, this layer is
--      driven by an explicit task on a two-minute schedule.
--   2. Calling a stored procedure (ML training, Cortex batches, enqueue).
--   3. Anything with a budget, which must be rate-limited by schedule rather
--      than by refresh lag.
--   4. Snapshotting, where the point is to record history rather than to
--      reflect current state.
--
-- The chain is built with AFTER dependencies, so it runs as one DAG with a
-- single root rather than five schedules racing each other.
--
--   T_ROOT (2 min)
--     └─ T_SYNDROMES ────── MATCH_RECOGNIZE, all six patterns
--          ├─ T_MATCH_ROWS ─ ALL ROWS PER MATCH symbol tagging
--          └─ T_SNAPSHOT ─── per-minute activity history
--               └─ T_BOUNDARY ── fix the single train/detect split point
--                    ├─ T_FORECAST ── ML.FORECAST
--                    │    └─ T_ANOMALY ── ML.ANOMALY_DETECTION
--                    │         └─ T_DRIVERS ── TOP_INSIGHTS
--                    └─ T_NOTES ───── AI_COMPLETE
--                         └─ T_TRIAGE ── AI_CLASSIFY over the cached notes
--                              └─ T_BRIEF ── AI_AGG
--                                   └─ T_ATTEST ── enqueue for the bridge
--
-- ONE PROCEDURE PER TASK, deliberately. A multi-statement `AS BEGIN ... END;`
-- body is valid Snowflake, and it is also unparseable by any client that splits
-- on semicolons — including scripts/run_sql.py, which would create the task with
-- only its first CALL and then execute the rest as orphaned statements. That
-- failure is silent: the task exists, it just does a third of its job.
--
-- Splitting into one call per task avoids the hazard entirely and buys a better
-- DAG: each stage gets its own row in TASK_HISTORY with its own duration and
-- return value, which is what makes the Pipeline tab legible.
-- tests/run_all.py asserts no bare BEGIN survives outside a $$ block.
--
-- Every task is created suspended. RESUME order is children-first, root-last:
-- Snowflake refuses to resume a child whose predecessor is suspended, and
-- resuming the root first would fire a run against a half-resumed DAG.
-- ===========================================================================

USE DATABASE ${SNOWFLAKE_DATABASE};
USE SCHEMA MARTS;

-- ---------------------------------------------------------------------------
-- Root. Cheap on purpose: it is a heartbeat that anchors the DAG, not work.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TASK MARTS.T_ROOT
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    SCHEDULE  = '2 minute'
    COMMENT   = 'DAG heartbeat. Records a tick so refresh lag is observable.'
AS
    INSERT INTO REF.BUILD_LOG (build_id, script_name, started_at, finished_at, status, detail)
    SELECT 'TASK', 'T_ROOT', CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), 'TICK',
           'epochs=' || (SELECT COUNT(*) FROM MARTS.EPOCH_STATES);

-- ---------------------------------------------------------------------------
-- The syndrome scan. This is the one that matters.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TASK MARTS.T_SYNDROMES
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     MARTS.T_ROOT
    COMMENT   = 'MATCH_RECOGNIZE over the epoch state sequence. Six clinical patterns.'
AS
    CALL MARTS.SP_BUILD_SYNDROMES();

CREATE OR REPLACE TASK MARTS.T_MATCH_ROWS
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     MARTS.T_SYNDROMES
    COMMENT   = 'ALL ROWS PER MATCH + CLASSIFIER(): per-epoch symbols for the timeline.'
AS
    CALL MARTS.SP_BUILD_MATCH_ROWS();

-- ---------------------------------------------------------------------------
-- Time series. Snapshot first, then boundary, then the two ML functions.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TASK ML.T_SNAPSHOT
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     MARTS.T_SYNDROMES
    COMMENT   = 'Per-minute activity history. MERGE, so a re-run replaces rather than duplicates.'
AS
    CALL ML.SP_SNAPSHOT_ACTIVITY();

CREATE OR REPLACE TASK ML.T_BOUNDARY
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     ML.T_SNAPSHOT
    COMMENT   = 'Fix the single train/detect split point, so the two views cannot disagree.'
AS
    CALL ML.SP_SET_BOUNDARY();

CREATE OR REPLACE TASK ML.T_FORECAST
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     ML.T_BOUNDARY
    COMMENT   = 'ML.FORECAST on the per-dog activity index. Trains on ts < boundary.'
AS
    CALL ML.SP_RUN_FORECAST();

CREATE OR REPLACE TASK ML.T_ANOMALY
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     ML.T_FORECAST
    COMMENT   = 'ML.ANOMALY_DETECTION. Detects on ts >= boundary. No window overlap.'
AS
    CALL ML.SP_RUN_ANOMALY();

CREATE OR REPLACE TASK ML.T_DRIVERS
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     ML.T_ANOMALY
    COMMENT   = 'TOP_INSIGHTS over the deviation metric. Produces a finding, not a chart.'
AS
    CALL ML.SP_RUN_TOP_INSIGHTS();

-- ---------------------------------------------------------------------------
-- The Cortex chain. Notes, then triage OVER those notes, then the pack brief
-- over all of them. Strictly sequential because each stage reads the previous
-- stage's cached table — triage classifies the generated note so the label and
-- the document a human reads cannot disagree.
--
-- Chained rather than scheduled independently: three separate schedules would
-- triple the chance of an unattended overnight run walking into the trial's
-- daily AI Function credit cap.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TASK AI.T_NOTES
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     ML.T_BOUNDARY
    COMMENT   = 'AI_COMPLETE: SOAP handoff notes. Capped, deduped on the finding key.'
AS
    CALL AI.SP_GENERATE_NOTES();

CREATE OR REPLACE TASK AI.T_TRIAGE
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     AI.T_NOTES
    COMMENT   = 'AI_CLASSIFY over the cached notes, not over the raw evidence.'
AS
    CALL AI.SP_GENERATE_TRIAGE();

CREATE OR REPLACE TASK AI.T_BRIEF
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     AI.T_TRIAGE
    COMMENT   = 'AI_AGG: one pack-wide brief over every cached note.'
AS
    CALL AI.SP_GENERATE_PACK_BRIEF();

CREATE OR REPLACE TASK ORACLE.T_ATTEST
    WAREHOUSE = ${SNOWFLAKE_WAREHOUSE}
    AFTER     AI.T_BRIEF
    COMMENT   = 'Stage findings for the bridge. Snowflake queues; it never signs.'
AS
    CALL ORACLE.SP_ENQUEUE_ATTESTATIONS();

-- ---------------------------------------------------------------------------
-- Resume, children first. A child cannot be resumed while its predecessor is
-- suspended, and resuming the root first would fire a run against a DAG that
-- is only half awake.
-- ---------------------------------------------------------------------------
ALTER TASK ORACLE.T_ATTEST   RESUME;
ALTER TASK AI.T_AI           RESUME;
ALTER TASK ML.T_DRIVERS      RESUME;
ALTER TASK ML.T_ML           RESUME;
ALTER TASK ML.T_SNAPSHOT     RESUME;
ALTER TASK MARTS.T_MATCH_ROWS RESUME;
ALTER TASK MARTS.T_SYNDROMES RESUME;
ALTER TASK MARTS.T_ROOT      RESUME;

-- ---------------------------------------------------------------------------
-- Pipeline observability. Tab 9 reads these four views and nothing else.
-- ---------------------------------------------------------------------------

-- Live refresh lag per Dynamic Table node. This is the proof that the
-- architecture diagram is what Snowflake actually runs.
CREATE OR REPLACE VIEW MARTS.V_DAG_LAG AS
SELECT
    name                                    AS object_name,
    schema_name,
    target_lag_sec,
    maximum_lag_sec,
    mean_lag_sec,
    latest_data_timestamp,
    scheduling_state:state::STRING          AS state,
    scheduling_state:reason_message::STRING AS reason
FROM TABLE(INFORMATION_SCHEMA.DYNAMIC_TABLE_REFRESH_HISTORY())
QUALIFY ROW_NUMBER() OVER (PARTITION BY name ORDER BY data_timestamp DESC) = 1;

CREATE OR REPLACE VIEW MARTS.V_TASK_HISTORY AS
SELECT
    name                AS task_name,
    database_name,
    schema_name,
    state,
    scheduled_time,
    completed_time,
    DATEDIFF('millisecond', query_start_time, completed_time) AS duration_ms,
    return_value,
    error_message
FROM TABLE(INFORMATION_SCHEMA.TASK_HISTORY(
    SCHEDULED_TIME_RANGE_START => DATEADD('hour', -24, CURRENT_TIMESTAMP()),
    RESULT_LIMIT => 500
));

-- Credit burn to date. Doubles as the cost guard.
CREATE OR REPLACE VIEW MARTS.V_CREDIT_BURN AS
SELECT
    DATE_TRUNC('hour', start_time)     AS hour,
    warehouse_name,
    ROUND(SUM(credits_used), 4)        AS credits,
    ROUND(SUM(SUM(credits_used)) OVER (ORDER BY DATE_TRUNC('hour', start_time)), 4)
                                       AS cumulative_credits
FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
WHERE start_time > DATEADD('day', -7, CURRENT_TIMESTAMP())
GROUP BY 1, 2;

-- One row. The header strip on the Pipeline tab.
CREATE OR REPLACE VIEW MARTS.V_PIPELINE_STATS AS
SELECT
    (SELECT COUNT(*) FROM RAW.COLLAR_TELEMETRY_BULK)          AS raw_rows_bulk,
    (SELECT COUNT(*) FROM RAW.COLLAR_TELEMETRY)               AS raw_rows_live,
    (SELECT COUNT(*) FROM STAGING.EPOCH_FEATURES)             AS epochs_live,
    (SELECT COUNT(*) FROM MARTS.EPOCH_STATES)                 AS epochs_classified,
    (SELECT COUNT(*) FROM MARTS.SYNDROME_MATCHES)             AS syndrome_matches,
    (SELECT COUNT(DISTINCT dog_id) FROM MARTS.SYNDROME_MATCHES) AS dogs_with_findings,
    (SELECT COUNT(*) FROM MARTS.SYNDROME_SENSITIVITY)         AS sensitivity_matches,
    (SELECT COUNT(*) FROM AI.VET_NOTES)                       AS vet_notes,
    (SELECT COUNT(*) FROM ORACLE.PUBLISH_QUEUE
      WHERE status = 'CONFIRMED')                             AS attestations_onchain,
    (SELECT ROUND(holdout_accuracy * 100, 2) FROM ML.MODEL_SUMMARY) AS holdout_accuracy_pct,
    (SELECT MAX(epoch_ts) FROM MARTS.EPOCH_STATES)            AS latest_epoch_ts,
    (SELECT SUM(n_rows) FROM RAW.INGEST_LOG
      WHERE landed_at > DATEADD('minute', -1, CURRENT_TIMESTAMP())) AS rows_last_minute,
    CURRENT_TIMESTAMP()                                       AS as_of;
