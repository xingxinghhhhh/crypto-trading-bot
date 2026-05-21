from __future__ import annotations

import logging
import re
from typing import TextIO

SENSITIVE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passphrase)\s*[:=]\s*[^,\s]+"
)


def redact_secret_text(message: str) -> str:
    return SENSITIVE_PATTERN.sub(lambda match: match.group(1) + "=***REDACTED***", message)


class SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_secret_text(record.getMessage())
        record.args = ()
        return True


def setup_logging(level: int = logging.INFO, stream: TextIO | None = None, force: bool = False) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=stream,
        force=force,
    )
    redaction_filter = SecretRedactionFilter()
    logging.getLogger().addFilter(redaction_filter)
    for handler in logging.getLogger().handlers:
        handler.addFilter(redaction_filter)
