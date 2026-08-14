# What is real and what is simplified

Judges reward the labelled compromise and punish the hidden one. Everything
below is also labelled **in the data** — a column, not a comment — so you can
check each claim with a query rather than taking this file's word for it.

---

## The table

| Simplification | Why | How it is labelled |
|---|---|---|
| Telemetry is **replayed** from a published research dataset, not streamed from a live collar | No collar hardware in a weekend. The data itself is real: 45 dogs, 27 breeds, 100 Hz dual IMU, video-annotated at one-second resolution, peer-reviewed | `RAW.COLLAR_TELEMETRY.is_replay = TRUE` on every landed row |
| Syndrome patterns are **clinically motivated, not clinically validated** | Validation requires veterinary study design and ethics approval, not sixty hours | `REF.SYNDROME_CATALOGUE.clinical_rationale` cites behavioural reasoning, never a trial. Stated on every tab |
| Some ethogram states are **heuristic** rather than model-derived | The dataset's core protocol covers locomotion and posture. Fine-grained health behaviours (scratching, head shaking) may not be first-class labels | `MARTS.EPOCH_STATES.state_source ∈ {MODEL, RULES, HEURISTIC, GEOMETRY, CONTEXT, LOW_QUALITY}`, surfaced as a banner and a per-dog percentage in the UI |
| Demo deterioration is **injected** | A recorded demo needs the detector to fire on camera | `is_synthetic = TRUE`. Detection sees it, training never fits it, `--clean` removes it |
| Attestations go to **devnet**, and the bridge is **centralised** | This is an attestation bridge, not a decentralised oracle network | Stated here and on the Pipeline tab. Every row carries its `tx_signature` and an explorer link you can check |
| Shelter data is **Austin only** | One city publishes a decade of clean, current, public intake and outcome records | Named in the tab and in the post. Not generalised to national claims |
| Classification accuracy is reported on **held-out dogs** and is materially lower than row-split figures published elsewhere | Row splits leak: at 100 Hz, adjacent samples are near-identical, so a random split puts the same dog on both sides | `ML.MODEL_SUMMARY.holdout_accuracy`, printed large on the Drivers tab rather than buried |
| `SIT` and `PLAY` are **model-only** states | The rules fallback cannot separate sitting from standing without a per-dog posture reference | `REF.ETHOGRAM` documents it. No syndrome depends on either state |
| `TOP_INSIGHTS` may run as a **SQL contribution decomposition** instead of the native function | The Snowflake Top Insights call signature has moved between preview and GA | `ML.DRIVER_INSIGHTS.method ∈ {TOP_INSIGHTS, SQL_CONTRIBUTION}`, printed above the chart |

---

## Two sentences that cost nothing and buy a lot

**TELLTAIL is not a diagnostic device and nothing it produces substitutes for a
veterinarian.** It detects behavioural sequences and describes them. It does not
diagnose, and the Cortex prompt explicitly instructs the model not to.

**Full credit to** Vehkaoja, Somppi, Törnqvist, Valldeoriola Cardó, Kumpulainen,
Väätäjä, Majaranta, Surakka, Kujala and Vainio et al., University of Helsinki,
whose dataset makes this possible — *Description of Movement Sensor Dataset for
Dog Behavior Classification*, **Data in Brief**, 2022.

---

## Check the claims yourself

```sql
-- Every landed row is a replay, and which ones are synthetic
SELECT is_replay, is_synthetic, COUNT(*)
FROM RAW.COLLAR_TELEMETRY GROUP BY 1, 2;

-- How much of the state layer is model output vs a threshold
SELECT * FROM MARTS.V_STATE_PROVENANCE ORDER BY epochs DESC;

-- The honest accuracy, and the protocol that produced it
SELECT holdout_accuracy, holdout_dogs, macro_f1, classifier, protocol
FROM ML.MODEL_SUMMARY;

-- Training never saw a synthetic row; detection did
SELECT COUNT(*) FROM ML.V_ACTIVITY_TRAIN  t
  JOIN ML.ACTIVITY_HISTORY h ON h.dog_id::VARCHAR = t.series AND h.ts = t.ts
 WHERE h.is_synthetic;                    -- expect 0

-- Cortex was never called from a render path: every call has a batch id
SELECT fn, batch_id, n_calls, ran_at FROM AI.USAGE_LOG ORDER BY ran_at DESC;

-- The pattern printed beside a finding is the pattern that produced it
SELECT syndrome_code, pattern_text, define_text FROM REF.SYNDROME_CATALOGUE;
```

---

## What we did *not* simplify, and could have

These are the places where the shortcut was available and refused.

**The split.** A random row split would have reported an accuracy roughly twenty
points higher and nobody would have checked. Whole dogs are held out, and the
holdout is breed-stratified and deterministic so it cannot be quietly re-rolled
until it flatters the model.

**The quantifiers.** Every syndrome runs at three strictness settings and the
curve is published (`MARTS.V_SENSITIVITY_CURVE`). A single hand-tuned quantifier
is a magic number.

**The epoch gate.** Epochs with fewer than 60 of an expected 100 samples are
`UNKNOWN` rather than classified on thin evidence, and the syndrome layer skips
them. This *reduces* the number of findings.

**The gap guard.** `MATCH_RECOGNIZE` matches rows that are consecutive in the
partition, which is not the same as consecutive in time. Matches spanning more
wall-clock seconds than they have epochs are discarded, so a "sequence" cannot
be assembled across a hole in the feed.

**The session boundary.** Patterns partition by `(dog_id, test_num)`, not
`dog_id`. Half a syndrome at the end of one session and half at the start of the
next does not join into a phantom finding.

**The smoothing filter.** A three-point despeckle would have made every pattern
fire more often — and it would have deleted the single-epoch head shake that S1
is *defined* by. States whose singleton occurrence is the clinical event are
exempt (`REF.ETHOGRAM.singleton_diagnostic`), and there is a test pinning that
behaviour so it cannot regress silently.

**The confidence score.** `MARTS.F_CONFIDENCE` is one documented formula —
0.45·evidence + 0.35·epoch quality + 0.20·model purity — applied uniformly. It
is not tuned per syndrome to make anything look better.
