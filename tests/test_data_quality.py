import json
import subprocess
import sys
from pathlib import Path

from crypto_bot.market.data_quality import validate_ohlcv_csv


def test_validate_data_reports_clean_csv_and_exports_json(tmp_path):
    csv_path = tmp_path / "bars.csv"
    export_path = tmp_path / "quality.json"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2024-01-01 00:00:00,10,12,9,11,100",
                "2024-01-01 01:00:00,11,13,10,12,120",
                "2024-01-01 02:00:00,12,14,11,13,130",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_ohlcv_csv(csv_path, "1h", export_path=export_path)

    assert report.valid is True
    assert report.exists is True
    assert report.columns_ok is True
    assert report.bar_count == 3
    assert report.start_time == "2024-01-01T00:00:00+00:00"
    assert report.end_time == "2024-01-01T02:00:00+00:00"
    assert report.missing_bar_count == 0
    assert report.duplicate_timestamp_count == 0
    assert report.issues == []

    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert payload["valid"] is True
    assert payload["bar_count"] == 3
    assert payload["missing_bar_count"] == 0


def test_validate_data_detects_quality_problems(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
                "2024-01-01 00:00:00,10,12,9,11,100",
                "2024-01-01 02:00:00,12,13,11,12.5,120",
                "2024-01-01 02:00:00,12,13,11,12.5,120",
                "2024-01-01 01:00:00,11,10,9,12,130",
                "bad-timestamp,11,12,10,11.5,100",
                "2024-01-01 04:00:00,11,12,10,,100",
                "2024-01-01 05:00:00,11,12,10,11.5,-1",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_ohlcv_csv(csv_path, "1h")

    assert report.valid is False
    assert report.timestamp_parse_error_count == 1
    assert report.timestamp_monotonic_increasing is False
    assert report.duplicate_timestamp_count == 1
    assert report.missing_value_count == 1
    assert report.ohlc_error_count == 1
    assert report.volume_error_count == 1
    assert report.missing_bar_count == 1
    assert report.time_gap_count == 1
    assert "timestamp_parse_failed" in report.issues
    assert "duplicate_timestamp" in report.issues
    assert "time_gap_detected" in report.issues


def test_validate_data_reports_missing_file(tmp_path):
    report = validate_ohlcv_csv(tmp_path / "missing.csv", "1h")

    assert report.valid is False
    assert report.exists is False
    assert report.bar_count == 0
    assert "csv_not_found" in report.issues


def test_validate_data_requires_exact_columns(tmp_path):
    csv_path = tmp_path / "wrong_columns.csv"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close",
                "2024-01-01 00:00:00,10,12,9,11",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = validate_ohlcv_csv(csv_path, "1h")

    assert report.valid is False
    assert report.columns_ok is False
    assert report.columns == ["timestamp", "open", "high", "low", "close"]
    assert "invalid_columns" in report.issues


def test_validate_data_cli_prints_report_and_exports_json(tmp_path):
    csv_path = tmp_path / "bars.csv"
    export_path = tmp_path / "report.json"
    csv_path.write_text(
        "\n".join(
            [
                "timestamp,open,high,low,close,volume",
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
            "validate-data",
            "--csv",
            str(csv_path),
            "--timeframe",
            "1h",
            "--export",
            str(export_path),
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "data_quality_valid: true" in completed.stdout
    assert "bar_count: 2" in completed.stdout
    assert export_path.exists()
    assert json.loads(export_path.read_text(encoding="utf-8"))["valid"] is True
