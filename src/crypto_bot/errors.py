class CryptoBotError(Exception):
    """Base exception for the trading bot."""


class ConfigError(CryptoBotError):
    """Raised when configuration is invalid."""


class SafetyError(CryptoBotError):
    """Raised when a protected live-trading path is reached."""


class RiskError(CryptoBotError):
    """Raised when an order violates risk or execution constraints."""


class MarketDataError(CryptoBotError):
    """Raised when public market data cannot be loaded safely."""
