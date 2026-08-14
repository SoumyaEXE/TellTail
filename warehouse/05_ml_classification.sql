-- ===========================================================================
-- 05_ml_classification.sql   ·   the ethogram classifier
--
-- Every epoch gets exactly one state, and the split that produces the accuracy
-- number is BY DOG, never by row.
--
-- Why that matters more than anything else in this file: at 100 Hz, samples
-- 40 ms apart are near-identical. A random row split puts adjacent samples from
-- the same dog on both sides, the model memorises the individual, and the
-- reported accuracy is a fiction. The literature on this dataset reports
-- single-subject classifiers falling from ~91% to ~70-74% when generalising to
-- dogs they were not trained on. Hold out whole dogs, report the lower number,
-- and print it large rather than burying it.
-- ===========================================================================

USE DATABASE ${SNOWFLAKE_DATABASE};
USE SCHEMA ML;

-- ---------------------------------------------------------------------------
-- The labelled epoch set, with raw labels mapped to ethogram states.
--
-- IDENTIFIERS ARE NOT FEATURES. dog_id, test_num and epoch_ts are carried for
-- the split and for joining back, and are dropped before the model sees a row.
-- Leaving dog_id in the feature vector would let the model learn "dog 23 mostly
-- trots" and would invalidate the entire holdout protocol.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW ML.V_LABELLED_EPOCHS AS
SELECT
    e.dog_id,
    e.test_num,
    e.epoch_ts,
    m.state                                            AS state,

    -- ---- feature vector (25 columns) ----
    e.vm_neck_mean, e.vm_neck_std, e.vm_neck_range, e.energy_neck, e.sma_neck,
    e.jerk_neck_mean, e.jerk_neck_std, e.zcr_neck,
    e.vm_back_mean, e.vm_back_std, e.sma_back, e.energy_back,
    e.gyro_neck_mean, e.gyro_back_mean,
    e.yaw_mean, e.yaw_abs_mean, e.yaw_consistency,
    e.pitch_neck_mean, e.pitch_var, e.roll_neck_mean, e.roll_var, e.pitch_back_mean,
    e.neck_back_corr, e.neck_dominance, e.activity_index
FROM STAGING.V_EPOCH_ALL e
JOIN REF.LABEL_MAP m
      ON m.raw_label = e.label_primary
     AND m.source_column = 'label_primary'
CROSS JOIN REF.V_PARAM p
WHERE m.state IS NOT NULL
  AND e.label_primary IS NOT NULL
  AND e.n_samples >= p.o:epoch_min_samples::NUMBER   -- quality gate
  AND e.neck_back_corr IS NOT NULL                   -- a constant epoch has no correlation
  AND NOT e.is_synthetic;                            -- training NEVER fits injected rows

-- Train: every dog not held out. Feature columns only + the target.
CREATE OR REPLACE VIEW ML.V_TRAIN AS
SELECT
    state,
    vm_neck_mean, vm_neck_std, vm_neck_range, energy_neck, sma_neck,
    jerk_neck_mean, jerk_neck_std, zcr_neck,
    vm_back_mean, vm_back_std, sma_back, energy_back,
    gyro_neck_mean, gyro_back_mean,
    yaw_mean, yaw_abs_mean, yaw_consistency,
    pitch_neck_mean, pitch_var, roll_neck_mean, roll_var, pitch_back_mean,
    neck_back_corr, neck_dominance, activity_index
FROM ML.V_LABELLED_EPOCHS
WHERE dog_id NOT IN (SELECT dog_id FROM REF.HOLDOUT_DOGS);

-- Test: entire dogs the model has never seen. Keeps dog_id so per-dog accuracy
-- is reportable — generalisation is not uniform across individuals and that is
-- itself a finding.
CREATE OR REPLACE VIEW ML.V_TEST AS
SELECT *
FROM ML.V_LABELLED_EPOCHS
WHERE dog_id IN (SELECT dog_id FROM REF.HOLDOUT_DOGS);

CREATE OR REPLACE VIEW ML.V_SPLIT_SUMMARY AS
SELECT
    IFF(dog_id IN (SELECT dog_id FROM REF.HOLDOUT_DOGS), 'holdout', 'train') AS split,
    COUNT(DISTINCT dog_id)   AS dogs,
    COUNT(*)                 AS epochs,
    COUNT(DISTINCT state)    AS states
FROM ML.V_LABELLED_EPOCHS
GROUP BY 1;

CREATE OR REPLACE VIEW ML.V_CLASS_BALANCE AS
SELECT
    state,
    COUNT(*)                                                        AS epochs,
    ROUND(100.0 * COUNT(*) / NULLIF(SUM(COUNT(*)) OVER (), 0), 2)   AS pct,
    COUNT(DISTINCT dog_id)                                          AS dogs
FROM ML.V_LABELLED_EPOCHS
GROUP BY state;

-- ---------------------------------------------------------------------------
-- The transparent SQL rules ethogram.
--
-- This is not a stub. If SNOWFLAKE.ML.CLASSIFICATION is unavailable on the
-- account, or if REF.PARAMS.use_rules_classifier is set to 1, this is the
-- classifier — every threshold is a row in REF.PARAMS, on screen, tunable, and
-- labelled state_source='RULES' wherever it appears. The syndromes are the
-- submission, not the classifier; losing the model must not lose the day.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW ML.V_RULES_STATE AS
SELECT
    e.dog_id, e.test_num, e.epoch_ts,
    CASE
        WHEN e.vm_neck_std < p.o:rules_rest_vm_std_max::FLOAT
         AND e.pitch_var   < p.o:rules_rest_pitch_var_max::FLOAT       THEN 'REST'
        WHEN e.vm_neck_std > p.o:shake_vm_std_min::FLOAT
         AND e.neck_back_corr < p.o:shake_corr_max::FLOAT
         AND e.neck_dominance > p.o:neck_dominance_min::FLOAT          THEN 'SHAKE'
        WHEN e.vm_neck_std BETWEEN p.o:scratch_vm_std_min::FLOAT
                               AND p.o:scratch_vm_std_max::FLOAT
         AND e.neck_back_corr < p.o:scratch_corr_max::FLOAT
         AND e.neck_dominance > p.o:neck_dominance_min::FLOAT          THEN 'SCRATCH'
        -- SNIFF before the locomotion rules. S6 (GI discomfort) is built on
        -- SNIFF and CIRCLE, so omitting it here would leave the rules-only path
        -- unable to express one of the six syndromes.
        WHEN e.pitch_neck_mean < p.o:rules_sniff_pitch_max::FLOAT
         AND e.vm_neck_std BETWEEN p.o:rules_sniff_vm_std_min::FLOAT
                               AND p.o:rules_sniff_vm_std_max::FLOAT   THEN 'SNIFF'
        WHEN e.neck_back_corr > p.o:rules_gallop_corr_min::FLOAT
         AND e.vm_neck_mean   > p.o:rules_gallop_vm_min::FLOAT         THEN 'GALLOP'
        WHEN e.neck_back_corr > p.o:rules_trot_corr_min::FLOAT
         AND e.vm_neck_mean   > p.o:rules_trot_vm_min::FLOAT           THEN 'TROT'
        WHEN e.neck_back_corr > p.o:rules_walk_corr_min::FLOAT
         AND e.vm_neck_mean   > p.o:rules_walk_vm_min::FLOAT           THEN 'WALK'
        ELSE 'STAND'
    END                                                               AS state,
    NULL::FLOAT                                                       AS confidence,
    'RULES'::STRING                                                   AS state_source
FROM STAGING.V_EPOCH_ALL e
CROSS JOIN REF.V_PARAM p;

-- ---------------------------------------------------------------------------
-- Train, evaluate, and pick a prediction source.
--
-- The procedure decides which of the two classifiers backs
-- ML.V_STATE_PREDICTION, so 06_marts_dt.sql has one stable interface to read
-- and does not care which path ran. Whichever it is, state_source says so in
-- the data, not in a comment.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE ML.SP_TRAIN_STATE_MODEL()
RETURNS STRING
LANGUAGE SQL
AS
$$
DECLARE
    use_rules  NUMBER  DEFAULT 0;
    n_train    NUMBER  DEFAULT 0;
    msg        STRING  DEFAULT '';
BEGIN
    SELECT COALESCE(value_num, 0) INTO :use_rules
      FROM REF.PARAMS WHERE key = 'use_rules_classifier';
    SELECT COUNT(*) INTO :n_train FROM ML.V_TRAIN;

    IF (:n_train = 0) THEN
        RETURN 'ABORT: ML.V_TRAIN is empty. Either the bulk load has not run, or '
            || 'REF.LABEL_MAP is unpopulated (Gate A output not pushed). '
            || 'Check RAW.V_LABEL_COVERAGE.';
    END IF;

    IF (:use_rules = 1) THEN
        CREATE OR REPLACE VIEW ML.V_STATE_PREDICTION AS
            SELECT dog_id, test_num, epoch_ts, state, confidence, state_source
            FROM ML.V_RULES_STATE;
        RETURN 'RULES classifier selected by REF.PARAMS.use_rules_classifier=1 ('
            || :n_train || ' labelled epochs available)';
    END IF;

    BEGIN
        CREATE OR REPLACE SNOWFLAKE.ML.CLASSIFICATION ML.STATE_MODEL(
            INPUT_DATA     => SYSTEM$QUERY_REFERENCE('SELECT * FROM ML.V_TRAIN'),
            TARGET_COLNAME => 'STATE',
            CONFIG_OBJECT  => {'on_error': 'skip'}
        );

        -- The model's own internal evaluation. Useful, but NOT the headline
        -- number: its split is internal to the training set, so it still shares
        -- dogs across the fold.
        CREATE OR REPLACE TABLE ML.MODEL_EVAL AS
            SELECT * FROM TABLE(ML.STATE_MODEL!SHOW_EVALUATION_METRICS());

        -- Feature importance. A free win: if neck_back_corr ranks high, the
        -- feature that was invented for this build is the one the model leans
        -- on, and that is a chart rather than a claim.
        CREATE OR REPLACE TABLE ML.FEATURE_IMPORTANCE AS
            SELECT * FROM TABLE(ML.STATE_MODEL!SHOW_FEATURE_IMPORTANCE());

        CREATE OR REPLACE VIEW ML.V_STATE_PREDICTION AS
            SELECT
                e.dog_id, e.test_num, e.epoch_ts,
                pred:class::STRING                                      AS state,
                GREATEST(
                  COALESCE(pred:probability[pred:class::STRING]::FLOAT, 0)
                )                                                       AS confidence,
                'MODEL'::STRING                                         AS state_source
            FROM (
                SELECT
                    f.dog_id, f.test_num, f.epoch_ts,
                    ML.STATE_MODEL!PREDICT(OBJECT_CONSTRUCT(
                        'VM_NECK_MEAN',   f.vm_neck_mean,
                        'VM_NECK_STD',    f.vm_neck_std,
                        'VM_NECK_RANGE',  f.vm_neck_range,
                        'ENERGY_NECK',    f.energy_neck,
                        'SMA_NECK',       f.sma_neck,
                        'JERK_NECK_MEAN', f.jerk_neck_mean,
                        'JERK_NECK_STD',  f.jerk_neck_std,
                        'ZCR_NECK',       f.zcr_neck,
                        'VM_BACK_MEAN',   f.vm_back_mean,
                        'VM_BACK_STD',    f.vm_back_std,
                        'SMA_BACK',       f.sma_back,
                        'ENERGY_BACK',    f.energy_back,
                        'GYRO_NECK_MEAN', f.gyro_neck_mean,
                        'GYRO_BACK_MEAN', f.gyro_back_mean,
                        'YAW_MEAN',       f.yaw_mean,
                        'YAW_ABS_MEAN',   f.yaw_abs_mean,
                        'YAW_CONSISTENCY',f.yaw_consistency,
                        'PITCH_NECK_MEAN',f.pitch_neck_mean,
                        'PITCH_VAR',      f.pitch_var,
                        'ROLL_NECK_MEAN', f.roll_neck_mean,
                        'ROLL_VAR',       f.roll_var,
                        'PITCH_BACK_MEAN',f.pitch_back_mean,
                        'NECK_BACK_CORR', f.neck_back_corr,
                        'NECK_DOMINANCE', f.neck_dominance,
                        'ACTIVITY_INDEX', f.activity_index
                    ))                                                  AS pred
                FROM STAGING.V_EPOCH_ALL f
                WHERE f.neck_back_corr IS NOT NULL
            ) e;

        msg := 'MODEL trained on ' || :n_train || ' epochs from '
            || (SELECT COUNT(*) FROM (SELECT DISTINCT dog_id FROM ML.V_LABELLED_EPOCHS
                                      WHERE dog_id NOT IN (SELECT dog_id FROM REF.HOLDOUT_DOGS)))
            || ' dogs';
    EXCEPTION
        WHEN OTHER THEN
            -- CLASSIFICATION unavailable on this account/region. Do not lose the
            -- day: fall back, and record why in the data.
            CREATE OR REPLACE VIEW ML.V_STATE_PREDICTION AS
                SELECT dog_id, test_num, epoch_ts, state, confidence, state_source
                FROM ML.V_RULES_STATE;
            UPDATE REF.PARAMS SET value_num = 1 WHERE key = 'use_rules_classifier';
            msg := 'FALLBACK to RULES classifier — ML.CLASSIFICATION failed: '
                || SQLERRM || ' (recorded in REF.PARAMS.use_rules_classifier=1)';
    END;

    RETURN :msg;
END;
$$;

-- ---------------------------------------------------------------------------
-- THE HONEST NUMBER: evaluation on entirely held-out dogs.
--
-- Computed here rather than taken from SHOW_EVALUATION_METRICS, because that
-- reports the model's internal split, which shares dogs across the fold. This
-- is the number that goes on the Drivers tab in large type.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE ML.SP_EVALUATE_HOLDOUT()
RETURNS STRING
LANGUAGE SQL
AS
$$
BEGIN
    CREATE OR REPLACE TABLE ML.HOLDOUT_PREDICTIONS AS
    SELECT
        t.dog_id, t.test_num, t.epoch_ts,
        t.state                       AS actual_state,
        pr.state                      AS predicted_state,
        pr.confidence,
        pr.state_source,
        IFF(t.state = pr.state, 1, 0) AS is_correct
    FROM ML.V_TEST t
    JOIN ML.V_STATE_PREDICTION pr
      ON pr.dog_id  = t.dog_id
     AND pr.test_num = t.test_num
     AND pr.epoch_ts = t.epoch_ts;

    -- Confusion matrix, long form. The dashboard pivots it into a heatmap.
    CREATE OR REPLACE TABLE ML.CONFUSION_MATRIX AS
    SELECT
        actual_state,
        predicted_state,
        COUNT(*)                                                              AS n,
        ROUND(100.0 * COUNT(*) /
              NULLIF(SUM(COUNT(*)) OVER (PARTITION BY actual_state), 0), 2)   AS pct_of_actual
    FROM ML.HOLDOUT_PREDICTIONS
    GROUP BY actual_state, predicted_state;

    -- Per-class precision / recall / F1 on unseen dogs.
    CREATE OR REPLACE TABLE ML.CLASS_METRICS AS
    WITH per_class AS (
        SELECT
            s.state,
            SUM(IFF(h.actual_state = s.state AND h.predicted_state = s.state, 1, 0)) AS tp,
            SUM(IFF(h.actual_state <> s.state AND h.predicted_state = s.state, 1, 0)) AS fp,
            SUM(IFF(h.actual_state = s.state AND h.predicted_state <> s.state, 1, 0)) AS fn,
            SUM(IFF(h.actual_state = s.state, 1, 0))                                  AS support
        FROM (SELECT DISTINCT actual_state AS state FROM ML.HOLDOUT_PREDICTIONS) s
        CROSS JOIN ML.HOLDOUT_PREDICTIONS h
        GROUP BY s.state
    )
    SELECT
        state, tp, fp, fn, support,
        ROUND(tp / NULLIF(tp + fp, 0), 4)                                    AS precision,
        ROUND(tp / NULLIF(tp + fn, 0), 4)                                    AS recall,
        ROUND(2.0 * tp / NULLIF(2.0 * tp + fp + fn, 0), 4)                   AS f1
    FROM per_class;

    -- Per-dog accuracy: generalisation is not uniform across individuals, and
    -- the spread is more informative than the mean.
    CREATE OR REPLACE TABLE ML.HOLDOUT_BY_DOG AS
    SELECT
        h.dog_id,
        d.breed,
        COUNT(*)                                     AS epochs,
        ROUND(AVG(h.is_correct), 4)                  AS accuracy
    FROM ML.HOLDOUT_PREDICTIONS h
    LEFT JOIN REF.DOG_INFO d ON d.dog_id = h.dog_id
    GROUP BY h.dog_id, d.breed;

    CREATE OR REPLACE TABLE ML.MODEL_SUMMARY AS
    SELECT
        (SELECT value_str FROM REF.PARAMS WHERE key = 'model_version')       AS model_version,
        (SELECT MAX(state_source) FROM ML.HOLDOUT_PREDICTIONS)               AS classifier,
        (SELECT COUNT(*) FROM ML.V_TRAIN)                                    AS train_epochs,
        (SELECT COUNT(DISTINCT dog_id) FROM ML.V_LABELLED_EPOCHS
          WHERE dog_id NOT IN (SELECT dog_id FROM REF.HOLDOUT_DOGS))         AS train_dogs,
        (SELECT COUNT(*) FROM ML.HOLDOUT_PREDICTIONS)                        AS holdout_epochs,
        (SELECT COUNT(*) FROM REF.HOLDOUT_DOGS)                              AS holdout_dogs,
        (SELECT ROUND(AVG(is_correct), 4) FROM ML.HOLDOUT_PREDICTIONS)       AS holdout_accuracy,
        (SELECT ROUND(AVG(f1), 4) FROM ML.CLASS_METRICS)                     AS macro_f1,
        (SELECT ROUND(SUM(f1 * support) / NULLIF(SUM(support),0), 4)
           FROM ML.CLASS_METRICS)                                            AS weighted_f1,
        'Dog-disjoint. Whole dogs held out, never rows. Row splits leak adjacent '
        || '100 Hz samples across the fold and inflate accuracy by ~20 points.'  AS protocol,
        CURRENT_TIMESTAMP()                                                  AS evaluated_at;

    RETURN 'holdout accuracy: '
        || (SELECT TO_VARCHAR(ROUND(100 * holdout_accuracy, 2)) FROM ML.MODEL_SUMMARY)
        || '% on ' || (SELECT holdout_dogs FROM ML.MODEL_SUMMARY) || ' unseen dogs'
        || ' | macro F1 ' || (SELECT macro_f1 FROM ML.MODEL_SUMMARY);
END;
$$;
