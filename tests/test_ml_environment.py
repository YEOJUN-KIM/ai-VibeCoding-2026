import unittest
from pathlib import Path

from auto_trader.ml.verify_environment import build_example_data, verify_environment


class MlEnvironmentTests(unittest.TestCase):
    def test_example_data_has_expected_schema(self):
        features, target = build_example_data()

        self.assertEqual(
            list(features.columns), ["return_5m", "volume_ratio", "market_return"]
        )
        self.assertEqual(len(features), len(target))
        self.assertEqual(set(target), {0, 1})

    def test_model_can_be_saved_and_reloaded(self):
        model_path = Path("artifacts/ml/generated/test_environment_check.joblib")
        self.addCleanup(model_path.unlink, missing_ok=True)
        result = verify_environment(model_path)

        self.assertTrue(model_path.is_file())
        self.assertTrue(result["round_trip_verified"])
        self.assertEqual(result["samples"], 8)


if __name__ == "__main__":
    unittest.main()
