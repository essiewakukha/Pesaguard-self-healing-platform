"""Structured JSON logging.

One JSON object per line means CloudWatch Logs Insights (Phase 4) can filter
and count by field, e.g. all failed payments handled by version v1.2.0.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Extra fields we allow callers to attach with logger.info(..., extra={...}).
_FIELDS = (
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "payment_id",
    "version",
    "color",
    "fault",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name in _FIELDS:
            value = getattr(record, name, None)
            if value is not None:
                entry[name] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # uvicorn's own access log would duplicate ours.
    logging.getLogger("uvicorn.access").disabled = True
