"""Logging for the app and the worker (14 Oct 2026).

- Standard output: one JSON object per line (MAILR_LOG_FORMAT=json, the default) or the old text
  line (text). Docker keeps it, rotated by the compose file's logging options.
- Files: the same lines in `<data_dir>/logs/<process>.log` (MAILR_LOG_DIR), rotated at
  MAILR_LOG_FILE_MB, MAILR_LOG_FILES files kept. They are on the data volume, so the history
  survives a redeploy, which recreates the containers and with them Docker's logs.
- Requests: uvicorn's access log is off (cli.py). The request middleware (core/slowlog.py) writes
  one line per request on `mailr.access`: method, route template, status, milliseconds, user id,
  statements and their time. Never the raw path or the query string, so no search text, address
  or id from a URL reaches a log.
- Values: a failed query's parameters and a validation error's input are left out of every line
  and traceback (core/errors.py hide_values; the engine hides query parameters too, db.py).

JSON fields: ts, level, logger, msg, and the extra fields a call passes (`extra={"route": …}`);
`exc` holds a traceback.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import UTC, datetime
from pathlib import Path

from mailr.config import get_settings
from mailr.core.errors import hide_values

NOISY = ("httpx", "httpcore", "imapclient", "aiosmtplib")
# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}
_configured: str | None = None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": hide_values(record.getMessage())}
        for k, v in vars(record).items():
            if k not in _STD and not k.startswith("_"):
                out[k] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
        if record.exc_info:
            out["exc"] = hide_values(self.formatException(record.exc_info))
        return json.dumps(out, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return hide_values(super().format(record))


TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _formatter() -> logging.Formatter:
    return JsonFormatter() if get_settings().log_format.lower() == "json" else TextFormatter(TEXT_FORMAT)


def log_dir() -> Path | None:
    s = get_settings()
    if s.log_dir.strip().lower() == "off":
        return None
    return Path(s.log_dir) if s.log_dir else Path(s.data_dir) / "logs"


def configure(process: str) -> None:
    """Set up the root logger for `process` ("app" or "worker") once. Handlers that someone else
    put on the root logger (pytest's capture) stay."""
    global _configured
    if _configured is not None:
        return
    _configured = process
    s = get_settings()
    root = logging.getLogger()
    root.setLevel(s.log_level)
    fmt = _formatter()
    if not any(getattr(h, "_mailr", False) for h in root.handlers):
        out = logging.StreamHandler()
        out.setFormatter(fmt)
        out._mailr = True  # type: ignore[attr-defined]
        root.addHandler(out)
        d = log_dir()
        if d is not None:
            try:
                d.mkdir(parents=True, exist_ok=True)
                fh = logging.handlers.RotatingFileHandler(
                    d / f"{process}.log", maxBytes=s.log_file_mb * 1024 * 1024, backupCount=s.log_files,
                    encoding="utf-8", delay=True)
                fh.setFormatter(fmt)
                fh._mailr = True  # type: ignore[attr-defined]
                root.addHandler(fh)
            except OSError as e:
                root.warning("log files off: %s cannot be written (%s)", d, type(e).__name__)
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
    # uvicorn's own loggers go through the handlers above (cli.py passes log_config=None).
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


def configured() -> bool:
    return _configured is not None


def reset_for_tests() -> None:
    """Forget the configuration (tests only): the next configure() sets up again."""
    global _configured
    _configured = None
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_mailr", False)]:
        root.removeHandler(h)
        h.close()
