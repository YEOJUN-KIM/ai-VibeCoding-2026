# Baseline BUY Candidate Reconstruction + First ML Experiment v1

## Result

**CANDIDATE_RECONSTRUCTION_NOT_READY**. The real dataset was hash-verified and
replayed twice deterministically. The user's section 5 stop condition applies:
no Logistic Regression or HistGradientBoosting training was run. No authenticated
candidate mask was written into Dataset v1. Existing dataset files are unchanged.

This is a completed feasibility audit and diagnostic replay, **not a completed
model comparison**. Model construction and chronological split utilities exist
and are tested; training, out-of-fold scoring, hold-out analysis and importance
reporting remain deferred until candidate provenance is adequate.

## Actual strategy analysis

Sources: `auto_trader/trading/strategies/moving_average.py`,
`auto_trader/trading/engine.py` (`step`, `_record_ml_decision`, `_buy`).
Strategy 17 uses short=10, long=40 (all 73704
full-history decision contexts confirm 10/40). Current config is descriptive:
DRY_RUN, AMOUNT 300000 KRW, 09:00–15:30, cooldown 60 minutes, daily order limit 30.
It is not proof of historical config outside recorded contexts.

* The engine requires >=40 closes and retains the latest 40 closes. The short
  average uses the final 10. The long average uses all supplied 40 / 40.
* ABOVE means short > long; equality is BELOW. BELOW clears armed and count.
  ABOVE increments confirmation_count; a previous BELOW arms entry. Starting
  ABOVE with no previous trend does not arm entry.
* A BUY requires armed, count >=2, last close > first close, quote.price >=
  short average, and zero strategy position. A previous ABOVE followed by BELOW
  can emit SELL. Repeated calls without a new bar emit HOLD without advancing state.
* A gap >2 minutes between processed bars resets previous trend/confirmation/armed.
  Lifecycle configure/reset also clears state. Exit handling runs before signals
  and a symbol that exited in that invocation skips entry evaluation.
* BUY intent enters `_buy` (risk, cash, sizing and order restrictions). Filled BUY
  clears entry_armed; blocked BUY keeps it. Hence pure signal intent and recorded
  BUY_FILLED / BUY_BLOCKED / WAIT are different objects. A strategy position can
  suppress the signal even before the risk manager.
* `_record_ml_decision` stores entry_armed and strategy_quantity after those side
  effects. Those fields are not a complete pre-risk state snapshot. `price` is the
  invocation quote, not necessarily the completed candle close.

## Diagnostic replay policy and PIT

The adapter calls the production MovingAverageStrategy directly; no signal rule
was copied or changed. It assumes position=0 and price=anchor close, never places
orders, never consumes fills, and never reads decision actions while replaying.
These assumptions generate repeated flat opportunities and must not be silently
called the original strategy's baseline candidates.

It replays all stock-only eligible 40-minute contiguous same-day windows before
joining usable dataset keys, so missing index features do not erase stock state
transitions. Every used candle must be valid, completed and available_at <=
prediction_at; delayed/backfilled windows are excluded. Prediction follows the
dataset's max(event+1m, available_at), maximum 5-minute delay policy. Future paths
are used only for outcome diagnostics. Dataset PIT validation is reused for features.
Actual invocation order, skipped bars and historical quote path are not recoverable
from this minute grid. This restriction is why replay is diagnostic only.

## Actual data results

| Measure | Count |
|---|---:|
| Usable samples | 89,368 |
| Raw flat/close replay BUY opportunities | 17,696 |
| Diagnostic opportunities joined to usable samples | 17,274 |
| Dates / symbols | 14 / 22 |
| Historical BUY_FILLED + BUY_BLOCKED events at dataset cutoff | 9,014 |
| Historical filled / blocked events | 232 / 8,782 |
| Historical BUY events in usable sample scope | 8,853 |
| Exact symbol/event timestamp overlap | 7,252 |
| Diagnostic candidate-only | 10,022 |
| Historical decision-only | 1,601 |
| Filled / blocked overlap | 136 / 7,116 |
| Comparable recorded quote / close pairs | 73,692 |
| Quote differs from anchor close | 55,629 |
| Comparable recorded / replay MA pairs | 66,388 |
| MA mismatch pairs | 0 |
| Filled rows recorded with entry_armed=False | 232 |
| Diagnostic opportunities with 20m executable label | 15,865 |
| Missing 20m label | 1,409 |
| Positive / nonpositive diagnostic labels | 3,713 / 12,152 |
| Positive diagnostic label ratio | 23.4037% |

Missing-label reasons: `{"NO_EXECUTABLE_NEXT_BAR": 112, "SESSION_END": 1297}`.
The binary definition is executable_net_return_20m >0; zero belongs to nonpositive.
Per-symbol counts are in candidate-by-symbol.csv and candidate-summary.json.

Comparison uses strategy 17 only, receipt <= dataset snapshot cutoff, and unique
(symbol, candle_event_at) keys. The earlier live DB count may be larger because
collection continued after the dataset cutoff. Historical BUY means FILLED plus
BLOCKED; filled-only numbers are also shown. Historical actions are retrospective
comparison evidence only, never candidate truth or target labels.

Recorded blocked overlap proves many candidate intents were rejected downstream.
Quote differences, post-fill state consumption, held positions, exits, polling and
resets explain why a flat/close grid replay can diverge. These are mechanisms,
not a proven per-row attribution: unavailable pre-state prevents exact attribution
of each candidate-only or decision-only event to risk, cash or position.

## Deferred experiment and interpretation

No authenticated baseline count, baseline mean/median return, baseline win rate,
ML BUY/SKIP count, retention, win-rate change, MFE/MAE change, candidate diagnostic
return sum, model coefficients, permutation importance or symbol hold-out result
is available. They are null in metrics.json. Diagnostic flat opportunities above
are not substituted for an authenticated baseline. No portfolio return, MDD or
CAGR is computed or implied. A future sum of candidate returns would be only a
candidate-level diagnostic sum, because candidates overlap.

Planned primary target: executable_net_return_20m >0, with the continuous outcome
preserved. Fixed probability threshold 0.5. Features come from the dataset manifest
excluding baseline_buy_candidate; an explicit leakage guard rejects outcome,
selection and symbol identity columns. Both models use train-only median imputation;
LR also uses train-only StandardScaler. HGB disables random early-stopping splits.
Expanding whole-date folds require at least 5 earlier dates and explicitly purge
training labels whose entry+20 minutes reaches validation. No folds were fitted,
no thresholds were tuned, and no final test performance is claimed.

## Reproduction and artifacts

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m auto_trader.ml.first_experiment
```

The command verifies all five dataset artifact SHA256 values before DB access,
uses a PostgreSQL REPEATABLE READ READ ONLY transaction, writes the audit, and
raises CANDIDATE_RECONSTRUCTION_NOT_READY (expected nonzero exit). It cannot be
forced through the gate by a CLI override.

Dataset hash: `94b7c402ed71a9e43a556f1a4a0d8bbe2f149938c55ac2be2baaa701a69d67e1`.

samples.csv SHA256: `4a6f56cd0412dc522da2d5049c1a58a5727f1cb5cd3add8b237c36d7566b9a7b`.

Replay content hash: `63a37fd5093e64e17a6915d63c0b6c117621ee9461e880f05f96750801aa9686`.

Small tracked artifacts: experiment-manifest.json, metrics.json,
candidate-summary.json, candidate-by-symbol.csv, report.md in this directory.
Large ignored replay: artifacts/ml/generated/experiments/v1/diagnostic-candidate-replay.csv.
No model binary was created. Manifest records seed, feature list, planned configs,
source hashes, evidence hashes and the read-only snapshot. Historical evidence
is receipt-cutoff scoped; later RAW corrections can change evidence hashes.

## Next step

Choose and version the candidate contract before training: either an explicit
flat/close counterfactual baseline with defined arming/consumption policy, or an
authenticated production replay using invocation-level pre-signal history,
quote, state, reset and exit provenance. This task does not add live recording or
change production behavior. More trading days alone cannot repair missing pre-state.

Candidate reconstruction: **NOT READY**

Best exploratory model: **N/A — not trained**

Baseline candidate count / ML selected count: **N/A / N/A**

Baseline / ML-filter mean executable net return: **N/A / N/A**

Baseline / ML-filter win rate: **N/A / N/A**

Verdict: **ML_FILTER_INCONCLUSIVE**

Profitability/generalization confidence: **NOT READY**

Recommendation: **MORE DATA / FEATURE OR LABEL REVIEW**, with candidate contract
and provenance review first. **PAPER ML FILTER EXPERIMENT is deferred.**

## Validation

Full Python regression: **371 passed**. Full JavaScript regression: **22 passed**.
Final ML targeted tests: **7 passed**. Test commands and log hashes are recorded
in validation.json. Existing production files and curated dataset artifacts were
not modified; all new files belong to ML code, tests or experiment artifacts.
