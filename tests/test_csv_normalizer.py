import subprocess
import sys
from pathlib import Path

import pandas as pd

from crypto_bot.market.csv_normalizer import normalize_ohlcv_csv


def test_normalize_csv_maps_common_columns_sorts_deduplicates_and_drops_extras(tmp_path):
    source = tmp_path / "external.csv"
    output = tmp_path / "BTC_USDT_1h.csv"
    source.write_text(
        "\n".join(
            [
                "symbol,Date,Open,High,Low,Close,Volume,tradecount",
                "BTCUSDT,2024-01-01 02:00:00,12,14,11,13,130,9",
                "BTCUSDT,2024-01-01 00:00:00,10,12,9,11,100,7",
                "BTCUSDT,2024-01-01 01:00:00,11,13,10,12,120,8",
                "BTCUSDT,2024-01-01 01:00:00,11,13,10,12.5,125,10",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = normalize_ohlcv_csv(source, output, timeframe="1h")

    frame = pd.read_csv(output)
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert frame["timestamp"].tolist() == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-01T01:00:00+00:00",
        "2024-01-01T02:00:00+00:00",
    ]
    assert frame["close"].tolist() == [11.0, 12.5, 13.0]
    assert result.input_rows == 4
    assert result.output_rows == 3
    assert result.dropped_duplicate_count == 1
    assert result.validation.valid is True


def test_normalize_csv_supports_unix_timestamp_seconds(tmp_path):
    source = tmp_path / "unix.csv"
    output = tmp_path / "normalized.csv"
    source.write_text(
        "\n".join(
            [
                "unix,open,high,low,close,volume",
                "1704067200,10,12,9,11,100",
                "1704070800,11,13,10,12,120",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = normalize_ohlcv_csv(source, output, timeframe="1h")

    frame = pd.read_csv(output)
    assert frame["timestamp"].tolist() == [
        "2024-01-01T00:00:00+00:00",
        "2024-01-01T01:00:00+00:00",
    ]
    assert result.validation.valid is True


def test_normalize_csv_supports_unix_timestamp_microseconds(tmp_path):
    source = tmp_path / "unix_microseconds.csv"
    output = tmp_path / "normalized.csv"
    source.write_text(
        "\n".join(
            [
                "open_time,open,high,low,close,volume",
                "1735689600000000,93576,94509.42,93489.03,94401.14,755.99",
                "1735693200000000,94401.14,95000,94000,94550,700",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = normalize_ohlcv_csv(source, output, timeframe="1h")

    frame = pd.read_csv(output)
    assert frame["timestamp"].tolist() == [
        "2025-01-01T00:00:00+00:00",
        "2025-01-01T01:00:00+00:00",
    ]
    assert result.validation.valid is True


def test_normalize_csv_supports_mixed_millisecond_and_microsecond_timestamps(tmp_path):
    source = tmp_path / "mixed_units.csv"
    output = tmp_path / "normalized.csv"
    source.write_text(
        "\n".join(
            [
                "open_time,open,high,low,close,volume",
                "1704067200000,10,12,9,11,100",
                "1735689600000000,93576,94509.42,93489.03,94401.14,755.99",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = normalize_ohlcv_csv(source, output, timeframe="1h")

    frame = pd.read_csv(output)
    assert frame["timestamp"].tolist() == [
        "2024-01-01T00:00:00+00:00",
        "2025-01-01T00:00:00+00:00",
    ]
    assert result.validation.missing_bar_count > 0


def test_normalize_csv_cli_runs_validation_after_writing_output(tmp_path):
    source = tmp_path / "external.csv"
    output = tmp_path / "BTC_USDT_1h.csv"
    report = tmp_path / "quality.json"
    source.write_text(
        "\n".join(
            [
                "time,Open,High,Low,Close,Volume",
                "2024-01-01T00:00:00Z,10,12,9,11,100",
                "2024-01-01T01:00:00Z,11,13,10,12,120",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "crypto_bot.cli",
            "normalize-csv",
            "--input",
            str(source),
            "--output",
            str(output),
            "--timeframe",
            "1h",
            "--export-report",
            str(report),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "normalized_csv:" in completed.stdout
    assert "data_quality_valid: true" in completed.stdout
    assert output.exists()
    assert report.exists()
    assert list(pd.read_csv(output).columns) == ["timestamp", "open", "high", "low", "close", "volume"]
