from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from crypto_bot.strategy.signals import Signal


class Strategy(ABC):
    @abstractmethod
    def generate_signal(self, symbol: str, bars: pd.DataFrame, timestamp: datetime) -> Signal:
        """Return a signal only; strategies must never place orders."""
