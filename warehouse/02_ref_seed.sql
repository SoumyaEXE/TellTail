-- ===========================================================================
-- 02_ref_seed.sql
--
-- Everything the pipeline treats as a decision rather than a computation lives
-- here as data: the ethogram vocabulary, the syndrome catalogue (patterns as
-- TEXT, so one definition drives the SQL engine, the sensitivity sweep and the
-- dashboard's code block), and every threshold.
--
-- No magic numbers below this file. If a number matters, it is a row in
-- REF.PARAMS and it is on screen in the Pipeline tab.
-- ===========================================================================

USE DATABASE ${SNOWFLAKE_DATABASE};
USE SCHEMA REF;

-- ---------------------------------------------------------------------------
-- 1. Dogs. Loaded by scripts/load_raw.py from DogInfo.csv.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS REF.DOG_INFO (
    dog_id      NUMBER      NOT NULL,
    breed       STRING,
    sex         STRING,
    age_years   FLOAT,
    weight_kg   FLOAT,
    height_cm   FLOAT,
    loaded_at   TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Cohorts. A Husky's normal is a Bulldog's emergency, so population averages
-- are useless and every comparison is either self or cohort.
CREATE OR REPLACE VIEW REF.V_DOG_COHORT AS
SELECT
    dog_id,
    breed,
    sex,
    age_years,
    weight_kg,
    height_cm,
    CASE WHEN age_years <  2 THEN 'junior'
         WHEN age_years <  7 THEN 'adult'
         WHEN age_years < 10 THEN 'mature'
         ELSE                     'senior' END                       AS age_band,
    CASE WHEN weight_kg < 15 THEN 'small'
         WHEN weight_kg < 25 THEN 'medium'
         WHEN weight_kg < 35 THEN 'large'
         ELSE                      'giant' END                       AS weight_band,
    CASE WHEN age_years <  2 THEN 'junior'
         WHEN age_years <  7 THEN 'adult'
         WHEN age_years < 10 THEN 'mature'
         ELSE                     'senior' END
      || '/' ||
    CASE WHEN weight_kg < 15 THEN 'small'
         WHEN weight_kg < 25 THEN 'medium'
         WHEN weight_kg < 35 THEN 'large'
         ELSE                      'giant' END                       AS cohort_id
FROM REF.DOG_INFO;

-- ---------------------------------------------------------------------------
-- 2. Raw label -> ethogram state. Populated from ref/column_map.json (Gate A
--    output) by scripts/run_sql.py immediately after this file runs.
--    Anything in the data and not in this table lands as UNMAPPED and is
--    counted, never silently dropped.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS REF.LABEL_MAP (
    raw_label     STRING NOT NULL,
    source_column STRING NOT NULL,
    state         STRING,
    n_rows        NUMBER,
    loaded_at     TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------------
-- 3. The ethogram. One row per state TELLTAIL can assign to an epoch.
--    `derivation` is the honesty column: MODEL states come from
--    SNOWFLAKE.ML.CLASSIFICATION, HEURISTIC states come from thresholds over
--    the feature layer and are labelled as such everywhere they appear.
-- ---------------------------------------------------------------------------
-- `singleton_diagnostic` is load-bearing and easy to overlook.
--
-- MARTS.EPOCH_STATES runs a three-point despeckle filter: an isolated epoch
-- flanked by two identical different states is replaced by them. That is the
-- right treatment for classifier flicker inside a long run — and it is
-- catastrophic for a state whose SINGLE-EPOCH OCCURRENCE IS THE FINDING.
--
-- S1 is REST SHAKE SCRATCH{3,} SHAKE SCRATCH{2,}: the head shake between two
-- scratch bouts is exactly one epoch, flanked by two identical SCRATCH epochs.
-- Unguarded smoothing deletes it, the alternation vanishes, and the syndrome
-- silently never fires. Same for the PAUSE in S2 and the alert STAND in S5.
--
-- So states that any pattern matches as a bare (unquantified) variable are
-- exempt from smoothing. Locomotion states are not: a single stray WALK inside
-- a scratch bout is noise, and removing it is what keeps itch{3,} contiguous.
CREATE OR REPLACE TABLE REF.ETHOGRAM (
    state            STRING  NOT NULL,
    display_name     STRING,
    family           STRING,          -- posture | locomotion | neck_dominant | derived
    derivation       STRING,          -- MODEL | HEURISTIC | CONTEXT
    is_neck_dominant BOOLEAN,
    singleton_diagnostic BOOLEAN,     -- exempt from the despeckle filter
    colour_hex       STRING,
    sort_order       NUMBER,
    description      STRING
);

INSERT INTO REF.ETHOGRAM
    (state, display_name, family, derivation, is_neck_dominant,
     singleton_diagnostic, colour_hex, sort_order, description)
SELECT * FROM VALUES
 ('REST',    'Resting',      'posture',       'MODEL',     FALSE, TRUE,  '#A8A29E',  1, 'Lying on chest. Low vector magnitude, stable pitch. Singleton onset in S1.'),
 ('SIT',     'Sitting',      'posture',       'MODEL',     FALSE, FALSE, '#D6D3D1',  2, 'Upright, stationary, pitch distinct from lying. Model-only; the rules fallback reads it as STAND.'),
 ('STAND',   'Standing',     'posture',       'MODEL',     FALSE, TRUE,  '#E7E5E4',  3, 'Upright, stationary. Singleton alert stand in S5, singleton rise in S4.'),
 ('WALK',    'Walking',      'locomotion',    'MODEL',     FALSE, FALSE, '#BFDBFE',  4, 'Both sensors in phase, low cadence.'),
 ('TROT',    'Trotting',     'locomotion',    'MODEL',     FALSE, FALSE, '#60A5FA',  5, 'Both sensors in phase, mid cadence.'),
 ('GALLOP',  'Galloping',    'locomotion',    'MODEL',     FALSE, FALSE, '#1D4ED8',  6, 'Both sensors in phase, high magnitude.'),
 ('SNIFF',   'Sniffing',     'posture',       'MODEL',     FALSE, FALSE, '#FDE68A',  7, 'Treat-searching. Head down, low translation.'),
 ('PLAY',    'Playing',      'locomotion',    'MODEL',     FALSE, FALSE, '#93C5FD',  8, 'High variance, irregular, both sensors active. Model-only.'),
 ('SHAKE',   'Head shake',   'neck_dominant', 'HEURISTIC', TRUE,  TRUE,  '#B45309',  9, 'Neck-dominant, high frequency, sensors decoupled. Singleton in S1 — never smoothed.'),
 ('SCRATCH', 'Scratching',   'neck_dominant', 'HEURISTIC', TRUE,  FALSE, '#D97706', 10, 'Neck-dominant, sustained, sensors decoupled. Always quantified in patterns.'),
 ('PAUSE',   'Pause',        'derived',       'CONTEXT',   FALSE, TRUE,  '#FCA5A5', 11, 'Still epoch bracketed by locomotion. Singleton stride interruption in S2.'),
 ('PACE',    'Pacing',       'derived',       'CONTEXT',   FALSE, FALSE, '#F59E0B', 12, 'Locomotion with repeated yaw reversal. Back-and-forth.'),
 ('CIRCLE',  'Circling',     'derived',       'CONTEXT',   FALSE, FALSE, '#EA580C', 13, 'Sustained same-direction yaw, low forward translation.'),
 ('SLOW_TRANSITION','Slow rise','derived',    'CONTEXT',   FALSE, TRUE,  '#C2410C', 14, 'Pitch variance rises while magnitude stays low. Singleton lever-up in S4.'),
 ('UNKNOWN', 'Unknown',      'derived',       'CONTEXT',   FALSE, TRUE,  '#F5F5F4', 98, 'Epoch failed the sample-count quality gate. Never smoothed over.')
AS v(state, display_name, family, derivation, is_neck_dominant, singleton_diagnostic,
     colour_hex, sort_order, description);

-- ---------------------------------------------------------------------------
-- 4. Tunable parameters. Every threshold in the build reads from here.
--    value_num is the payload; description is what goes on screen.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE REF.PARAMS (
    key         STRING NOT NULL,
    value_num   FLOAT,
    value_str   STRING,
    unit        STRING,
    description STRING
);

INSERT INTO REF.PARAMS (key, value_num, value_str, unit, description)
SELECT * FROM VALUES
 -- epoch quality
 ('epoch_min_samples',        60,   NULL, 'samples', 'Epochs with fewer 100 Hz samples than this are UNKNOWN, not guessed.'),
 ('gravity_ref',              1.0,  NULL, 'g',       'Resting |a| baseline. 1.0 if the file is in g, 9.81 if m/s^2. Set at Gate A.'),
 -- state smoothing: a 1-second classifier is noisy; MATCH_RECOGNIZE needs
 -- contiguity, so a mode filter over a small odd window buys pattern stability
 -- without inventing states. Window is in epochs (seconds).
 ('state_smooth_window',      3,    NULL, 'epochs',  'Mode filter half-window*2+1 applied to raw states before pattern matching.'),
 -- neck-dominant heuristics (used only where SHAKE/SCRATCH are not labelled)
 ('shake_vm_std_min',         0.90, NULL, 'g',       'SHAKE: neck vector-magnitude SD floor.'),
 ('shake_corr_max',           0.25, NULL, 'r',       'SHAKE: neck/back correlation ceiling. Sensors decouple.'),
 ('scratch_vm_std_min',       0.40, NULL, 'g',       'SCRATCH: neck vector-magnitude SD floor.'),
 ('scratch_vm_std_max',       0.90, NULL, 'g',       'SCRATCH: neck vector-magnitude SD ceiling.'),
 ('scratch_corr_max',         0.35, NULL, 'r',       'SCRATCH: neck/back correlation ceiling.'),
 ('neck_dominance_min',       1.30, NULL, 'ratio',   'SCRATCH/SHAKE: neck SD must exceed back SD by this factor.'),
 -- rules-classifier fallback (only if SNOWFLAKE.ML.CLASSIFICATION is unavailable)
 ('rules_rest_vm_std_max',    0.05, NULL, 'g',       'Rules fallback: REST ceiling on neck SD.'),
 ('rules_rest_pitch_var_max', 0.02, NULL, 'rad',     'Rules fallback: REST ceiling on pitch SD.'),
 ('rules_gallop_corr_min',    0.70, NULL, 'r',       'Rules fallback: GALLOP correlation floor.'),
 ('rules_gallop_vm_min',      1.60, NULL, 'g',       'Rules fallback: GALLOP magnitude floor.'),
 ('rules_trot_corr_min',      0.60, NULL, 'r',       'Rules fallback: TROT correlation floor.'),
 ('rules_trot_vm_min',        1.25, NULL, 'g',       'Rules fallback: TROT magnitude floor.'),
 ('rules_walk_corr_min',      0.50, NULL, 'r',       'Rules fallback: WALK correlation floor.'),
 ('rules_walk_vm_min',        1.08, NULL, 'g',       'Rules fallback: WALK magnitude floor.'),
 -- SNIFF in the fallback matters more than it looks: S6 (GI discomfort) is
 -- built on SNIFF and CIRCLE, so without this rule the rules-only path cannot
 -- express one of the six syndromes at all. Head-down orientation with modest
 -- neck activity and little whole-body translation.
 ('rules_sniff_pitch_max',   -0.25, NULL, 'rad',     'Rules fallback: SNIFF ceiling on mean neck pitch. Negative = head down.'),
 ('rules_sniff_vm_std_min',   0.05, NULL, 'g',       'Rules fallback: SNIFF floor on neck SD. Above a still posture.'),
 ('rules_sniff_vm_std_max',   0.45, NULL, 'g',       'Rules fallback: SNIFF ceiling on neck SD. Below a scratch bout.'),
 ('use_rules_classifier',     0,    NULL, 'bool',    '1 = bypass ML.CLASSIFICATION and use the transparent SQL rules ethogram.'),
 -- derived context states
 ('pause_vm_std_max',         0.12, NULL, 'g',       'PAUSE: stillness ceiling on neck SD.'),
 ('pause_neighbour_epochs',   2,    NULL, 'epochs',  'PAUSE: locomotion must occur within this many epochs either side.'),
 ('pace_yaw_consistency_max', 0.35, NULL, 'ratio',   'PACE: |mean yaw| / mean|yaw| ceiling. Low = reversing direction.'),
 ('pace_yaw_activity_min',    0.25, NULL, 'rad/s',   'PACE: mean|yaw| floor, so standing still is not pacing.'),
 ('circle_yaw_consistency_min',0.70,NULL, 'ratio',   'CIRCLE: |mean yaw| / mean|yaw| floor. High = one direction.'),
 ('circle_yaw_activity_min',  0.35, NULL, 'rad/s',   'CIRCLE: mean|yaw| floor.'),
 ('circle_translation_max',   0.25, NULL, 'g',       'CIRCLE: dynamic back magnitude ceiling. Turning, not travelling.'),
 ('slowrise_pitch_var_min',   0.10, NULL, 'rad',     'SLOW_TRANSITION: pitch SD floor. The dog is changing posture.'),
 ('slowrise_vm_std_max',      0.35, NULL, 'g',       'SLOW_TRANSITION: neck SD ceiling. Slowly, not springing up.'),
 -- baselines
 ('baseline_window_epochs',   3600, NULL, 'epochs',  'Trailing self-baseline window, in one-second epochs (1 hour of dog time).'),
 ('baseline_min_epochs',      300,  NULL, 'epochs',  'Below this the baseline is null rather than noise.'),
 -- anomaly / forecast
 ('forecast_horizon',         60,   NULL, 'periods', 'ML.FORECAST horizon on the per-dog activity index.'),
 ('anomaly_detect_window',    900,  NULL, 'seconds', 'Detect window. Train is ts < T, detect is ts >= T, same T. No overlap.'),
 -- cortex budget
 ('cortex_max_rows_per_batch',${CORTEX_MAX_ROWS_PER_BATCH}, NULL, 'rows', 'Hard cap on new AI_COMPLETE calls per task run. Trial cap is ~10 credits/day.'),
 ('cortex_model',             NULL, '${CORTEX_MODEL}', 'model', 'Model used by AI_COMPLETE and AI_AGG.'),
 -- attestation
 ('attest_min_severity',      2,    NULL, 'level',   'Only findings at this triage severity or above are queued for chain.'),
 ('model_version',            NULL, 'state-v3+match-v1', 'tag', 'Stamped into every attestation payload.')
AS v(key, value_num, value_str, unit, description);

-- Single-row object of every numeric param. Downstream SQL does
--   CROSS JOIN REF.V_PARAM p ... p.o:shake_corr_max::FLOAT
-- which broadcasts one row instead of running a scalar subquery per row.
CREATE OR REPLACE VIEW REF.V_PARAM AS
SELECT OBJECT_AGG(key, TO_VARIANT(COALESCE(value_num, TRY_TO_DOUBLE(value_str)))) AS o
FROM REF.PARAMS
WHERE value_num IS NOT NULL OR TRY_TO_DOUBLE(value_str) IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 5. THE SYNDROME CATALOGUE.
--
--    pattern_text and define_text are the literal PATTERN and DEFINE clauses.
--    They are the single source of truth for three consumers:
--      a) the hand-written views in 07_syndromes.sql (the tuned variant)
--      b) MARTS.SP_SYNDROME_SWEEP, which builds MATCH_RECOGNIZE by EXECUTE
--         IMMEDIATE for the sensitivity curve
--      c) the Syndromes tab, which prints the clause beside the visualisation
--
--    Changing a pattern in one place changes it everywhere, including on screen.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE REF.SYNDROME_CATALOGUE (
    syndrome_code     STRING NOT NULL,
    syndrome_name     STRING,
    body_system       STRING,
    symbols           ARRAY,          -- pattern variable names, in order
    pattern_text      STRING,         -- tuned variant, the clinical default
    define_text       STRING,
    min_epochs        NUMBER,         -- minimum rows a match can span (for the
                                      -- specificity term in the confidence score)
    default_severity  NUMBER,         -- 1 routine, 2 schedule, 3 urgent
    clinical_rationale STRING,
    why_not_threshold  STRING
);

INSERT INTO REF.SYNDROME_CATALOGUE
SELECT
    column1, column2, column3, PARSE_JSON(column4), column5, column6,
    column7, column8, column9, column10
FROM VALUES
(
  'S1', 'Otitis / ear irritation', 'aural',
  '["onset","shake","itch","shake2","itch2"]',
  'onset shake itch{3,} shake2 itch2{2,}',
  'onset AS state = ''REST'', shake AS state = ''SHAKE'', itch AS state = ''SCRATCH'', shake2 AS state = ''SHAKE'', itch2 AS state = ''SCRATCH''',
  7, 2,
  'Head shake alternating with sustained scratch bouts, emerging from rest, is the classic presentation of external ear canal irritation. The alternation is the sign; either behaviour alone is normal grooming.',
  'Daily scratch count is normal in a flea-free dog. A totals-based tracker sees an unremarkable number. The clinical signal is the ALTERNATION of shake and scratch, and an average has no concept of order.'
),
(
  'S2', 'Intermittent lameness', 'musculoskeletal',
  '["stride","halt","stride2","halt2","stride3","halt3"]',
  'stride{3,} halt stride2{1,3} halt2 stride3{1,3} halt3',
  'stride AS state = ''WALK'', halt AS state = ''PAUSE'', stride2 AS state = ''WALK'', halt2 AS state = ''PAUSE'', stride3 AS state = ''WALK'', halt3 AS state = ''PAUSE''',
  8, 2,
  'A sound gait interrupted by repeated short pauses at shortening intervals. The dog offloads the limb, rests it, resumes. Weight-bearing lameness before it becomes a limp an owner can see.',
  'Step count is unchanged and daily distance is unchanged. Stride INTERRUPTION FREQUENCY is rising. This is the presentation an owner describes as "he seems fine, he just stops a lot now", and no aggregate captures it.'
),
(
  'S3', 'Exercise intolerance', 'cardiorespiratory',
  '["burst","recover","burst2","recover2"]',
  'burst+ recover{5,} burst2{1,2} recover2{8,}',
  'burst AS state = ''TROT'', recover AS state = ''REST'', burst2 AS state = ''TROT'', recover2 AS state = ''REST''',
  15, 3,
  'Activity bursts collapsing in length while recovery intervals lengthen. Reduced exercise tolerance is an early sign in cardiac and respiratory disease, and it appears in the shape of the day long before total activity falls.',
  'Total activity minutes are IDENTICAL. The same minutes are redistributed into shorter bursts with progressively longer recoveries. A daily total is definitionally blind to this; only the ordered burst/recovery ratio shows it.'
),
(
  'S4', 'Reluctance to rise (osteoarthritis onset)', 'musculoskeletal',
  '["settled","lever","rise","settled2"]',
  'settled{10,} lever rise settled2{10,}',
  'settled AS state = ''REST'', lever AS state = ''SLOW_TRANSITION'', rise AS state = ''STAND'', settled2 AS state = ''REST''',
  22, 2,
  'A long rest, a slow lever-up rather than a spring-up, a brief stand, and back down. Early degenerative joint disease shows in the cost of the transition, not in the amount of rest.',
  'Rest totals are unchanged; a threshold on "hours resting" fires on a healthy sleeping dog and misses this entirely. The TRANSITION is the finding, and a transition only exists between two states in order.'
),
(
  'S5', 'Separation distress', 'behavioural',
  '["alert","tread","alert2","tread2"]',
  'alert tread{4,} alert2 tread2{4,}',
  'alert AS state = ''STAND'', tread AS state = ''PACE'', alert2 AS state = ''STAND'', tread2 AS state = ''PACE''',
  10, 2,
  'Cyclic pacing punctuated by alert stands, repeating. The stand is the dog checking the door; the pacing is what happens between checks.',
  'Pacing exists in happy dogs and in bored dogs. A pacing-minutes threshold cannot separate them. The CYCLE — pace, check, pace, check — is what distinguishes distress, and a cycle is a sequence.'
),
(
  'S6', 'GI discomfort', 'gastrointestinal',
  '["probe","turn","probe2"]',
  'probe{5,} turn{2,} probe2{5,}',
  'probe AS state = ''SNIFF'', turn AS state = ''CIRCLE'', probe2 AS state = ''SNIFF''',
  12, 2,
  'Repeated ground-casting and circling in pre-elimination posture without resolution. Unproductive straining and repeated posturing is a recognised presentation of gastrointestinal discomfort.',
  'Sniffing is the single most common outdoor behaviour and circling happens before every normal elimination. Only the REPETITION WITHOUT RESOLUTION — cast, circle, cast again — is abnormal, and repetition is an ordering property.'
)
AS v(column1, column2, column3, column4, column5, column6, column7, column8, column9, column10);

-- ---------------------------------------------------------------------------
-- 6. Sensitivity variants. One axis only: quantifier strictness.
--    A single hand-tuned quantifier is a magic number and judges can smell it,
--    so every pattern runs at three settings and the curve gets published.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE REF.SYNDROME_VARIANTS (
    syndrome_code STRING NOT NULL,
    variant       STRING NOT NULL,   -- loose | tuned | strict
    pattern_text  STRING NOT NULL,
    min_epochs    NUMBER,
    note          STRING
);

INSERT INTO REF.SYNDROME_VARIANTS (syndrome_code, variant, pattern_text, min_epochs, note)
SELECT * FROM VALUES
 ('S1','loose',  'onset shake itch{2,} shake2 itch2{1,}',                     5,  'one fewer scratch epoch per bout'),
 ('S1','tuned',  'onset shake itch{3,} shake2 itch2{2,}',                     7,  'clinical default'),
 ('S1','strict', 'onset shake itch{5,} shake2 itch2{4,}',                     11, 'two more scratch epochs per bout'),

 ('S2','loose',  'stride{2,} halt stride2{1,4} halt2 stride3{1,4} halt3',     6,  'shorter stride runs accepted'),
 ('S2','tuned',  'stride{3,} halt stride2{1,3} halt2 stride3{1,3} halt3',     8,  'clinical default'),
 ('S2','strict', 'stride{5,} halt stride2{1,2} halt2 stride3{1,2} halt3',     10, 'longer initial stride, tighter recoveries'),

 ('S3','loose',  'burst+ recover{3,} burst2{1,3} recover2{5,}',               10, 'shorter recoveries accepted'),
 ('S3','tuned',  'burst+ recover{5,} burst2{1,2} recover2{8,}',               15, 'clinical default'),
 ('S3','strict', 'burst+ recover{8,} burst2{1,2} recover2{15,}',              25, 'recovery must more than double'),

 ('S4','loose',  'settled{5,} lever rise settled2{5,}',                       12, 'shorter flanking rests'),
 ('S4','tuned',  'settled{10,} lever rise settled2{10,}',                     22, 'clinical default'),
 ('S4','strict', 'settled{20,} lever rise settled2{20,}',                     42, 'sustained rest either side'),

 ('S5','loose',  'alert tread{3,} alert2 tread2{3,}',                         8,  'shorter pacing runs'),
 ('S5','tuned',  'alert tread{4,} alert2 tread2{4,}',                         10, 'clinical default'),
 ('S5','strict', 'alert tread{6,} alert2 tread2{6,}',                         14, 'sustained pacing between checks'),

 ('S6','loose',  'probe{3,} turn{1,} probe2{3,}',                               7,  'a single turn counts'),
 ('S6','tuned',  'probe{5,} turn{2,} probe2{5,}',                               12, 'clinical default'),
 ('S6','strict', 'probe{8,} turn{3,} probe2{8,}',                               19, 'prolonged casting either side')
AS v(syndrome_code, variant, pattern_text, min_epochs, note);

-- ---------------------------------------------------------------------------
-- 7. Dog-disjoint holdout. Deterministic and breed-stratified.
--
--    Random ROW splits leak: adjacent 100 Hz samples from the same dog land on
--    both sides of the split and the reported accuracy is a fiction. The
--    literature on this dataset reports single-subject classifiers falling from
--    ~91% to ~70-74% when generalising to unseen dogs. Hold out whole dogs and
--    report the honest number.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE REF.HOLDOUT_DOGS (
    dog_id   NUMBER,
    breed    STRING,
    reason   STRING
);

-- Populated after DOG_INFO loads (scripts/run_sql.py calls this, and 11_tasks
-- re-runs it if the roster changes).
CREATE OR REPLACE PROCEDURE REF.SP_ASSIGN_HOLDOUT(holdout_fraction FLOAT)
RETURNS STRING
LANGUAGE SQL
AS
$$
BEGIN
    -- Rank dogs deterministically inside each breed, then take the top slice of
    -- each breed. Deterministic because HASH() is stable, stratified because the
    -- ranking is per breed, so the holdout is not accidentally all Labradors.
    CREATE OR REPLACE TABLE REF.HOLDOUT_DOGS AS
    WITH ranked AS (
        SELECT
            d.dog_id,
            COALESCE(d.breed, 'unknown')                        AS breed,
            ROW_NUMBER() OVER (PARTITION BY COALESCE(d.breed,'unknown')
                               ORDER BY HASH(d.dog_id, 'telltail-holdout-v1')) AS rn,
            COUNT(*)    OVER (PARTITION BY COALESCE(d.breed,'unknown'))        AS n_breed
        FROM REF.DOG_INFO d
    )
    SELECT dog_id, breed,
           'breed-stratified deterministic holdout, fraction=' || :holdout_fraction AS reason
    FROM ranked
    WHERE rn <= GREATEST(1, FLOOR(n_breed * :holdout_fraction));

    RETURN 'holdout dogs: ' || (SELECT COUNT(*) FROM REF.HOLDOUT_DOGS)
        || ' of ' || (SELECT COUNT(*) FROM REF.DOG_INFO);
END;
$$;

-- ---------------------------------------------------------------------------
-- 8. Austin Animal Center. Written by scripts/austin_sync.py from Socrata.
--    Streamlit in Snowflake has no public internet egress, so the host writes
--    and the app only ever reads a table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS REF.AAC_INTAKES (
    animal_id        STRING,
    name             STRING,
    datetime         TIMESTAMP_NTZ,
    intake_type      STRING,
    intake_condition STRING,
    animal_type      STRING,
    sex_upon_intake  STRING,
    age_upon_intake  STRING,
    breed            STRING,
    color            STRING,
    found_location   STRING,
    raw_payload      VARIANT,
    synced_at        TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE TABLE IF NOT EXISTS REF.AAC_OUTCOMES (
    animal_id         STRING,
    name              STRING,
    datetime          TIMESTAMP_NTZ,
    date_of_birth     TIMESTAMP_NTZ,
    outcome_type      STRING,
    outcome_subtype   STRING,
    animal_type       STRING,
    sex_upon_outcome  STRING,
    age_upon_outcome  STRING,
    breed             STRING,
    color             STRING,
    raw_payload       VARIANT,
    synced_at         TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- Which shelter categories are behaviour-linked. Hand-curated, and kept as data
-- so the mapping is auditable rather than buried in a CASE inside a chart.
CREATE OR REPLACE TABLE REF.AAC_BEHAVIOUR_MAP (
    field       STRING,   -- intake_type | intake_condition | outcome_subtype
    value       STRING,
    is_behaviour BOOLEAN,
    maps_to_syndrome STRING,
    note        STRING
);

INSERT INTO REF.AAC_BEHAVIOUR_MAP
SELECT * FROM VALUES
 ('intake_type',     'Owner Surrender',  TRUE,  NULL, 'Surrender reasons include behaviour; the portal does not split them per record.'),
 ('intake_condition','Behavior',         TRUE,  'S5', 'Behavioural presentation recorded at intake.'),
 ('intake_condition','Medical',          FALSE, NULL, 'Medical, kept for contrast against the behavioural tail.'),
 ('intake_condition','Injured',          FALSE, 'S2', 'Injury: the endpoint S2 is trying to catch early.'),
 ('intake_condition','Sick',             FALSE, 'S6', 'Illness: the endpoint S6 is trying to catch early.'),
 ('intake_condition','Aged',             FALSE, 'S4', 'Age-related presentation, the S4 population.'),
 ('outcome_subtype', 'Behavior',         TRUE,  'S5', 'Behaviour named as the outcome reason.'),
 ('outcome_subtype', 'Aggressive',       TRUE,  'S5', 'Behavioural, at the far end.'),
 ('outcome_subtype', 'Suffering',        FALSE, NULL, 'Medical euthanasia.'),
 ('outcome_subtype', 'Medical',          FALSE, NULL, 'Medical outcome.')
AS v(field, value, is_behaviour, maps_to_syndrome, note);
