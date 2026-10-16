"""Compatibility import for the original moving-average execution engine."""

import logging  # Retains the existing logging patch point for integrations/tests.

from .trading import KST
from .trading.engine import TradingEngine

MovingAverageEngine = TradingEngine

__all__ = ["MovingAverageEngine", "TradingEngine", "KST"]
