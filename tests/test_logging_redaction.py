import io
import logging

from crypto_bot.logging import setup_logging


def test_logging_redacts_sensitive_values():
    stream = io.StringIO()
    setup_logging(stream=stream, force=True)

    logging.getLogger("crypto_bot.test").warning(
        "API_KEY=abc SECRET=def TOKEN=ghi PASSWORD=jkl PASSPHRASE=mno safe=value"
    )

    output = stream.getvalue()
    assert "abc" not in output
    assert "def" not in output
    assert "ghi" not in output
    assert "jkl" not in output
    assert "mno" not in output
    assert "safe=value" in output
    assert "API_KEY=***REDACTED***" in output
