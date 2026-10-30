"""Offline v1 feasibility gate. Never instantiate an engine, broker or recorder.

The flat/close replay is a diagnostic, NOT an authenticated candidate mask.
Current RAW lacks engine invocation/quote/pre-signal state provenance, so this
entry point deliberately stops before training. See the generated report.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import subprocess

from .data_readiness import (canonical, digest, encode, make_series, price_valid,
                             regular, time_valid, verify_artifacts, write_csv, KST)
from ..database import connect
from ..trading.strategies.base import SignalContext
from ..trading.strategies.moving_average import MovingAverageStrategy

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'flat-close-diagnostic-v1'
SEED = 17
TARGET = 'executable_net_return_20m'
MINUTE = timedelta(minutes=1)


def load_dataset(root=ROOT):
    manifest = json.loads((root/'artifacts/ml/datasets/v1/manifest.json').read_text('utf-8'))
    verify_artifacts(manifest)  # All five existing artifacts, before any DB or ML work.
    with (root/manifest['artifact_locations']['samples.csv']).open(encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    return manifest, rows


def load_evidence(manifest):
    """Restrict persisted observations to the dataset's receipt cutoff, READ ONLY."""
    cutoff = datetime.fromisoformat(manifest['snapshot']['as_of'])
    with connect() as conn:
        conn.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        meta = conn.execute('SELECT transaction_timestamp() AS transaction_at, '
                            'pg_current_snapshot()::text AS snapshot_id').fetchone()
        meta['transaction_read_only'] = conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']
        stocks = conn.execute("SELECT * FROM ml_raw_candles WHERE source='TOSS' "
                              "AND interval='1m' AND collected_at<=%s ORDER BY symbol,event_at,available_at",
                              (cutoff,)).fetchall()
        decisions = conn.execute('SELECT * FROM ml_strategy_decisions WHERE strategy_id=17 '
                                 'AND collected_at<=%s ORDER BY id', (cutoff,)).fetchall()
        config = conn.execute('SELECT id,short_period,long_period,execution_mode,sizing_mode,order_amount,'
                              'order_quantity,trading_start,trading_end,cooldown_minutes,daily_order_limit '
                              'FROM live_strategies WHERE id=17').fetchone()
    # Times in the config are descriptive, never features.
    return stocks, decisions, {k: str(v) for k, v in (config or {}).items()}, meta


def replay(stocks, short=10, long=40):
    """Reuse production signal logic with explicit flat-position/close-price policy.

    Replay every eligible stock window, including windows missing index features;
    filter to curated samples only AFTER replay, to avoid lost state transitions.
    Never consume a fill or a decision action. Never disarm on a synthetic BUY.
    Only completed, valid, same-day contiguous PIT windows are accepted.
    """
    strategy = MovingAverageStrategy()
    results = []
    for symbol, series in sorted(make_series(stocks, 'symbol', 'TOSS').items()):
        previous, armed, count, last = None, False, 0, None
        for at, anchor in sorted(series.items()):
            prediction = max(at+MINUTE, anchor['available_at'])
            if not regular(at) or prediction > at+5*MINUTE or prediction.astimezone(KST).date() != at.astimezone(KST).date():
                continue
            window = [series.get(at-i*MINUTE) for i in reversed(range(long))]
            if any(r is None or not regular(r['event_at']) or not time_valid(r) or not price_valid(r)
                   or r['event_at'].astimezone(KST).date() != at.astimezone(KST).date()
                   or r['available_at'] > prediction or r['event_at']+MINUTE > prediction for r in window):
                continue
            if last is None or at-last > 2*MINUTE:
                previous, armed, count = None, False, 0
            signal = strategy.generate_signal(SignalContext(
                history=[r['close_price'] for r in window], price=anchor['close_price'],
                short_period=short, long_period=long, previous_trend=previous,
                entry_armed=armed, confirmation_count=count, new_bar=True, position_quantity=lambda: 0))
            results.append(dict(symbol=symbol, candidate_event_at=at.isoformat(),
                                prediction_at=prediction.isoformat(), baseline_buy_candidate=signal.action == 'BUY',
                                candidate_reason=signal.reason, strategy_id=17, strategy_version=VERSION,
                                short_average=str(signal.short_average), long_average=str(signal.long_average),
                                trend=signal.trend, previous_trend=previous, entry_armed=signal.entry_armed,
                                confirmation_count=signal.confirmation_count,
                                feature_available_at_max=max(r['available_at'] for r in window).isoformat()))
            previous, armed, count, last = signal.trend, signal.entry_armed, signal.confirmation_count, at
    return results


def feature_list(manifest):
    features = [f for f in manifest['feature_list'] if f != 'baseline_buy_candidate']
    forbidden = ('future', 'executable', 'excursion', 'mfe', 'mae', 'entry_', 'usable',
                 'exclusion', 'flags', 'symbol', 'target', 'label', 'outcome', 'price_future')
    if any(any(x in f.lower() for x in forbidden) for f in features):
        raise ValueError('Outcome/identity/selection information in feature list')
    return features


def chronological_folds(rows, minimum_dates=5):
    """Whole-date expanding folds, with explicit outcome-end purge as well."""
    days = sorted({r['market_date'] for r in rows})
    folds = []
    for i in range(minimum_dates, len(days)):
        valid = [j for j, r in enumerate(rows) if r['market_date'] == days[i]]
        boundary = min(datetime.fromisoformat(rows[j]['prediction_at']) for j in valid)
        train = [j for j, r in enumerate(rows) if r['market_date'] < days[i]
                 and r.get('entry_event_at') and datetime.fromisoformat(r['entry_event_at'])+20*MINUTE < boundary]
        folds.append((train, valid))
    return folds


def make_models():
    """No new dependencies: train-only preprocessing in both sklearn Pipelines."""
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    return {
        'logistic_regression': make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True),
                                            StandardScaler(), LogisticRegression(C=1, max_iter=2000, random_state=SEED)),
        'hist_gradient_boosting': make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True),
                                                HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15,
                                                                               early_stopping=False, random_state=SEED)),
    }


def require_ready(summary):
    if summary['status'] != 'READY':
        raise RuntimeError('CANDIDATE_RECONSTRUCTION_NOT_READY')


def key(row):
    at = row.get('candidate_event_at') or row.get('prediction_event_at') or row.get('candle_event_at')
    return row['symbol'], at.isoformat() if isinstance(at, datetime) else at


def summarize(manifest, samples, replay_rows, decisions, stocks, config):
    sample_map = {key(s): s for s in samples}
    all_candidates = {key(r): r for r in replay_rows if r['baseline_buy_candidate']}
    candidates = {k: r for k, r in all_candidates.items() if k in sample_map}
    # Action is used ONLY here for retrospective comparison, never replay/target.
    historical = {key(r) for r in decisions if r['action'] in ('BUY_FILLED', 'BUY_BLOCKED', 'BUY')}
    filled = {key(r) for r in decisions if r['action'] == 'BUY_FILLED'}
    blocked = {key(r) for r in decisions if r['action'] == 'BUY_BLOCKED'}
    scoped = historical & sample_map.keys()
    lookup = {(r['symbol'], r['event_at']): r for r in stocks}
    comparable = [r for r in decisions if (r['symbol'], r.get('candle_event_at')) in lookup
                  and r['history_count'] == 40 and r.get('short_average') is not None]
    mismatches = sum(r['price'] != lookup[r['symbol'], r['candle_event_at']]['close_price'] for r in comparable)
    contexts = [r for r in decisions if r.get('history_count') == 40]
    replay_lookup = {key(r): r for r in replay_rows}
    ma_pairs = [(r, replay_lookup[key(r)]) for r in comparable if key(r) in replay_lookup]
    ma_mismatches = sum(r['short_average'] != Decimal(p['short_average'])
                        or r['long_average'] != Decimal(p['long_average']) for r, p in ma_pairs)
    labeled = [sample_map[k] for k in candidates if sample_map[k].get(TARGET)]
    missing = [sample_map[k] for k in candidates if not sample_map[k].get(TARGET)]
    exclusions = Counter()
    for r in missing:
        entry = datetime.fromisoformat(r['entry_event_at']).astimezone(KST) if r.get('entry_event_at') else None
        reason = ('NO_EXECUTABLE_NEXT_BAR' if not r.get('entry_event_at') else
                  'SESSION_END' if entry+19*MINUTE >= entry.replace(hour=15, minute=30, second=0, microsecond=0)
                  else 'FUTURE_PATH_MISSING')
        exclusions[reason] += 1
    positive = sum(float(s[TARGET]) > 0 for s in labeled)
    periods = Counter((r.get('metadata', {}).get('short_period'), r.get('metadata', {}).get('long_period')) for r in contexts)
    return dict(status='CANDIDATE_RECONSTRUCTION_NOT_READY',
                policy='DIAGNOSTIC ONLY: flat position, anchor close, all PIT complete stock windows, no synthetic fills',
                blockers=['Engine quote.price is not anchor close; minute RAW does not preserve the quote at each invocation.',
                          'entry_armed and strategy_quantity are recorded AFTER buy/exits; pre-signal state is not preserved.',
                          'RAW does not preserve exact engine invocation, skipped bars, startup/reset or exit-suppression sequence.',
                          'Flat replay never consumes fills; repeated candidates are counterfactual opportunities, not authenticated baseline candidates.'],
                current_strategy_17=config,
                observed_period_configs={f'{a}/{b}': n for (a, b), n in sorted(periods.items(), key=str)},
                comparable_quote_rows=len(comparable), quote_close_mismatch_rows=mismatches,
                comparable_ma_rows=len(ma_pairs), replay_recorded_ma_mismatch_rows=ma_mismatches,
                post_fill_disarmed_rows=sum(not r['entry_armed'] for r in decisions if r['action'] == 'BUY_FILLED'),
                raw_replay_candidates=len(all_candidates), reconstructed_diagnostic_candidates=len(candidates),
                candidate_dates=len({sample_map[k]['market_date'] for k in candidates}),
                candidate_by_symbol=dict(sorted(Counter(k[0] for k in candidates).items())),
                historical_buy_decision_rows=sum(r['action'] in ('BUY_FILLED', 'BUY_BLOCKED', 'BUY') for r in decisions),
                historical_buy_unique_events=len(historical), historical_filled_events=len(filled), historical_blocked_events=len(blocked),
                comparison_scope='strategy 17, receipt <= dataset cutoff; scoped comparison uses usable sample keys; sets deduplicate polling',
                scoped_historical_buy_events=len(scoped), timestamp_overlap=len(scoped & candidates.keys()),
                candidate_only=len(candidates.keys()-scoped), decision_only=len(scoped-candidates.keys()),
                filled_overlap=len(filled & candidates.keys()), blocked_overlap=len(blocked & candidates.keys()),
                diagnostic_labeled_candidates=len(labeled), missing_20m_label=len(missing), missing_label_reasons=dict(exclusions),
                positive_target_count=positive, nonpositive_target_count=len(labeled)-positive,
                positive_target_ratio=positive/len(labeled) if labeled else None,
                candidate_mask_hash=digest(replay_rows),
                training_executed=False, verdict='ML_FILTER_INCONCLUSIVE', profitability_generalization_confidence='NOT READY',
                next_step='MORE DATA / FEATURE OR LABEL REVIEW: establish a versioned pre-risk candidate contract before PAPER ML FILTER EXPERIMENT')


def main():
    manifest, samples = load_dataset()
    features = feature_list(manifest)
    stocks, decisions, config, snapshot = load_evidence(manifest)
    rows = replay(stocks)
    summary = summarize(manifest, samples, rows, decisions, stocks, config)
    # Verify deterministic replay on the actual evidence, not just a fixture.
    if digest(rows) != digest(replay(stocks)):
        raise RuntimeError('Non-deterministic candidate replay')
    output = ROOT/'artifacts/ml/experiments/v1'
    generated = ROOT/'artifacts/ml/generated/experiments/v1'
    output.mkdir(parents=True, exist_ok=True)
    generated.mkdir(parents=True, exist_ok=True)
    write_csv(generated/'diagnostic-candidate-replay.csv', rows)
    experiment = dict(experiment_version='v1', created_at=datetime.now(timezone.utc).isoformat(),
                      git_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                      dataset_version=manifest['dataset_version'], dataset_hash=manifest['dataset_hash'],
                      dataset_samples_sha256=manifest['artifacts']['samples.csv'],
                      candidate_reconstruction_version=VERSION, target=TARGET,
                      binary_target=f'{TARGET} > 0', feature_list=features,
                      train_dates=[], validation_dates=[], random_seed=SEED, threshold=0.5,
                      model_config={'logistic_regression': dict(C=1, max_iter=2000),
                                    'hist_gradient_boosting': dict(max_iter=100, max_leaf_nodes=15, early_stopping=False)},
                      preprocessing_config='Pipeline: train-only median imputer; LR train-only StandardScaler',
                      planned_split='expanding full dates, minimum 5 train dates; explicit 20m outcome-end purge',
                      evidence_hashes=dict(stocks=digest(stocks), decisions=digest(decisions)), evidence_snapshot=snapshot,
                      candidate_replay_hash=summary['candidate_mask_hash'], dataset_artifacts_verified=True,
                      actual_replay_determinism_verified=True, status=summary['status'],
                      source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
                                     ('auto_trader/ml/first_experiment.py', 'auto_trader/trading/strategies/moving_average.py',
                                      'auto_trader/trading/engine.py')})
    metrics = dict(status=summary['status'], verdict=summary['verdict'], training_executed=False,
                   baseline=None, logistic_regression=None, hist_gradient_boosting=None,
                   symbol_holdout=None, feature_importance=None,
                   reason='Readiness gate failed. No authenticated candidates, fitted models, predictions or performance claims.')
    for name, obj in [('experiment-manifest.json', experiment), ('candidate-summary.json', summary), ('metrics.json', metrics)]:
        (output/name).write_text(json.dumps(obj, default=encode, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    write_csv(output/'candidate-by-symbol.csv', [dict(symbol=s, diagnostic_candidates=n) for s, n in summary['candidate_by_symbol'].items()])
    (output/'report.md').write_text(report(summary, experiment), encoding='utf-8')
    print(canonical(summary))
    # Expected, explicit stop status: no model construction or fit before this gate.
    require_ready(summary)


def report(s, experiment):
    return f'''# Baseline BUY Candidate Reconstruction + First ML Experiment v1

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
Strategy 17 uses short=10, long=40 (all {sum(s['observed_period_configs'].values())}
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
| Raw flat/close replay BUY opportunities | {s['raw_replay_candidates']:,} |
| Diagnostic opportunities joined to usable samples | {s['reconstructed_diagnostic_candidates']:,} |
| Dates / symbols | {s['candidate_dates']} / {len(s['candidate_by_symbol'])} |
| Historical BUY_FILLED + BUY_BLOCKED events at dataset cutoff | {s['historical_buy_unique_events']:,} |
| Historical filled / blocked events | {s['historical_filled_events']:,} / {s['historical_blocked_events']:,} |
| Historical BUY events in usable sample scope | {s['scoped_historical_buy_events']:,} |
| Exact symbol/event timestamp overlap | {s['timestamp_overlap']:,} |
| Diagnostic candidate-only | {s['candidate_only']:,} |
| Historical decision-only | {s['decision_only']:,} |
| Filled / blocked overlap | {s['filled_overlap']:,} / {s['blocked_overlap']:,} |
| Comparable recorded quote / close pairs | {s['comparable_quote_rows']:,} |
| Quote differs from anchor close | {s['quote_close_mismatch_rows']:,} |
| Comparable recorded / replay MA pairs | {s['comparable_ma_rows']:,} |
| MA mismatch pairs | {s['replay_recorded_ma_mismatch_rows']:,} |
| Filled rows recorded with entry_armed=False | {s['post_fill_disarmed_rows']:,} |
| Diagnostic opportunities with 20m executable label | {s['diagnostic_labeled_candidates']:,} |
| Missing 20m label | {s['missing_20m_label']:,} |
| Positive / nonpositive diagnostic labels | {s['positive_target_count']:,} / {s['nonpositive_target_count']:,} |
| Positive diagnostic label ratio | {s['positive_target_ratio']:.4%} |

Missing-label reasons: `{json.dumps(s['missing_label_reasons'], sort_keys=True)}`.
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
.\\.venv\\Scripts\\python.exe -m auto_trader.ml.first_experiment
```

The command verifies all five dataset artifact SHA256 values before DB access,
uses a PostgreSQL REPEATABLE READ READ ONLY transaction, writes the audit, and
raises CANDIDATE_RECONSTRUCTION_NOT_READY (expected nonzero exit). It cannot be
forced through the gate by a CLI override.

Dataset hash: `{experiment['dataset_hash']}`.

samples.csv SHA256: `{experiment['dataset_samples_sha256']}`.

Replay content hash: `{s['candidate_mask_hash']}`.

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
'''


if __name__ == '__main__':
    main()
