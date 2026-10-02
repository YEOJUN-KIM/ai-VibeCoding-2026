"""Verify that the stage-1 ML environment can train and reload a model.

The tiny synthetic dataset in this module is only an installation smoke test.
It is not a trading model and must not be used to make orders.
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path
from typing import Any

import joblib
import matplotlib
import numpy as np
import pandas as pd
import scipy
import seaborn
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


FEATURE_NAMES = ["return_5m", "volume_ratio", "market_return"]


def build_example_data() -> tuple[pd.DataFrame, pd.Series]:
    """Return a deterministic toy dataset shaped like future market features."""
    features = pd.DataFrame(
        [
            (-0.021, 0.72, -0.010),
            (-0.014, 0.81, -0.006),
            (-0.008, 0.93, -0.003),
            (-0.004, 1.05, -0.001),
            (0.002, 1.08, 0.001),
            (0.006, 1.18, 0.003),
            (0.011, 1.31, 0.006),
            (0.018, 1.46, 0.009),
        ],
        columns=FEATURE_NAMES,
    )
    target = pd.Series([0, 0, 0, 0, 1, 1, 1, 1], name="positive_after_cost")
    return features, target


def verify_environment(model_path: str | Path) -> dict[str, Any]:
    """Train, save, reload, and compare a small deterministic classifier."""
    destination = Path(model_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    features, target = build_example_data()
    model = Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "classifier",
                LogisticRegression(solver="liblinear", random_state=2026),
            ),
        ]
    )
    model.fit(features, target)
    predictions_before = model.predict(features)

    joblib.dump(model, destination)
    restored_model = joblib.load(destination)
    predictions_after = restored_model.predict(features)
    round_trip_verified = bool(np.array_equal(predictions_before, predictions_after))
    if not round_trip_verified:
        raise RuntimeError("저장 전후 모델 예측이 일치하지 않습니다.")

    return {
        "purpose": "stage-1 environment smoke test; not a trading model",
        "python": platform.python_version(),
        "packages": {
            "joblib": joblib.__version__,
            "matplotlib": matplotlib.__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit-learn": sklearn.__version__,
            "scipy": scipy.__version__,
            "seaborn": seaborn.__version__,
        },
        "samples": len(features),
        "features": FEATURE_NAMES,
        "model_path": str(destination.resolve()),
        "predictions": predictions_after.tolist(),
        "round_trip_verified": round_trip_verified,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path("artifacts/ml/generated/environment_check.joblib"),
        help="검증용 모델을 저장할 경로",
    )
    args = parser.parse_args()
    print(json.dumps(verify_environment(args.model_path), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
