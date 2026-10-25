"""Error reporting to a Sentry-compatible server, such as a self-hosted GlitchTip.

Off unless MAILR_SENTRY_DSN is set. Then the API (FastAPI) and the worker send unhandled errors,
log lines at ERROR and above, and jobs that failed for good. The web app sends its own through
the app (api/error_routes.py), so its reports never leave the browser for another origin.

What a report never holds (scrub(), applied to every event before it leaves the process):
- request bodies, cookies, headers (Authorization and the CSRF token among them) and query
  strings: only the method and the path, with ids, are kept;
- local variables of the stack frames (they hold mail, addresses and keys) and breadcrumbs;
- email addresses in messages and exception texts, which become "<address>", and the values in
  database and validation errors (the engine hides them too: db.py hide_parameters);
- the user beyond their numeric id;
- in URL paths and transaction names, the same values as in other texts (addresses, tokens);
- the text of the exception and of the log line (only the exception type and the log line's
  template are kept) for an event raised while the request or job had read local-only data
  (core/outbound.py taint scope), and for every event when the DSN host is hosted (not on the local
  network, `dsn_service`). `mailr check-config` warns about a hosted DSN host.
No performance data is sent (no tracing). Reports carry the release (mailr@MAILR_RELEASE) and the
environment (MAILR_SENTRY_ENVIRONMENT).
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mailr.config import get_settings

log = logging.getLogger("mailr.errors")

_enabled = False
EMAIL = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[\w-]+(?:\.[\w-]+)+")
# Values in a database error ("[parameters: ...]") and in a validation error ("input_value=...",
# "'input': ..."): mail text, notes and names.
SQL_PARAMS = re.compile(r"\[parameters: .*?\](?=\s*(?:\n|\(|$))", re.S)
INPUT_VALUE = re.compile(r"input_value=.*?(?=, input_type=|$)", re.S)
INPUT_KEY = re.compile(r"""(['"]input['"]: ).*?(?=, ['"]\w+['"]: |\}|$)""", re.S)
# The driver's and Postgres's own texts that quote a value: "invalid input for query argument $1:
# 'text' (...)", 'invalid input syntax for type integer: "text"', "Key (email)=(a@b) already exists".
DB_VALUE = re.compile(r"""(query argument \$\d+: ).*?(?= \(|$)|(for type \w+: )"[^"\n]*"|(Key \([^)\n]*\)=)\(.*?\)(?= |$)""",
                      re.M)
BEARER = re.compile(r"(?i)\b(bearer|token|password|secret|key)([=: ]+)\S+")
MAX_TEXT = 2000


def enabled() -> bool:
    return _enabled


def release() -> str | None:
    r = get_settings().release.strip()
    return f"mailr@{r}" if r else None


def hide_values(text: str) -> str:
    """The text of an error without the values of a failed query or a validation error. Logs
    (core/logsetup.py) and error reports use it."""
    text = SQL_PARAMS.sub("[parameters hidden]", text)
    text = INPUT_VALUE.sub("input_value=<hidden>", text)
    text = INPUT_KEY.sub(r"\1<hidden>", text)
    return DB_VALUE.sub(lambda m: (m[1] or m[2] or m[3] or "") + "<hidden>", text)


def redact(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    text = hide_values(text)
    text = EMAIL.sub("<address>", text)
    text = BEARER.sub(lambda m: f"{m[1]}{m[2]}***", text)
    return text[:MAX_TEXT]


# Paths that carry a secret (an invite's token): the segment after these is replaced.
SECRET_PATH = re.compile(r"(/(?:api/v1/)?(?:invites|join)/)[^/?#]+")


def _strip_query(url: Any) -> Any:
    if not isinstance(url, str):
        return url
    try:
        u = urlsplit(url)
    except ValueError:
        return None
    return urlunsplit((u.scheme, u.netloc, _redact_path(u.path), "", ""))


def _redact_path(path: str) -> str:
    """A URL path with its secret segments and, segment by segment, its addresses and tokens hidden."""
    return "/".join(str(redact(seg)) for seg in SECRET_PATH.sub(r"\1***", path).split("/"))


_DSN_LOCAL: dict[str, bool] = {}


def dsn_service(dsn: str | None = None):
    """The error tracker as an outbound Service (core/outbound.py): local when every address of
    the DSN host is private or loopback, else hosted. Checked once per DSN and process."""
    from mailr.core import outbound

    dsn = (get_settings().sentry_dsn if dsn is None else dsn) or ""
    try:
        u = urlsplit(dsn)
        base = urlunsplit((u.scheme, u.hostname or "", "", "", "")) if u.hostname else ""
    except ValueError:
        base = ""
    if dsn not in _DSN_LOCAL:
        _DSN_LOCAL[dsn] = bool(base) and outbound.host_is_local_now(base)
    return outbound.Service(kind="error_tracker", name="sentry", base_url=base,
                            locality=outbound.LOCAL if _DSN_LOCAL[dsn] else outbound.HOSTED, source="sentry")


def _types_only(hint: dict[str, Any] | None, dsn: str | None = None) -> bool:
    """Keep only exception types: the event comes from a local-only scope, or the tracker is hosted."""
    from mailr.core import outbound

    if outbound.tainted():
        return True
    exc = ((hint or {}).get("exc_info") or (None, None, None))[1]
    if outbound.exception_tainted(exc):
        return True
    return not outbound.local(dsn_service(dsn))


def scrub(event: dict[str, Any], hint: dict[str, Any] | None = None, *, dsn: str | None = None) -> dict[str, Any] | None:
    """Remove everything personal from a report (module docstring). Used for the server's own
    reports (before_send) and for the web app's (the envelope relay)."""
    bare = _types_only(hint, dsn)
    req = event.get("request")
    if isinstance(req, dict):
        event["request"] = {k: v for k, v in {"method": req.get("method"), "url": _strip_query(req.get("url"))}.items()
                            if v}
    user = event.get("user")
    event["user"] = {"id": str(user["id"])} if isinstance(user, dict) and user.get("id") else {}
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    if "message" in event:
        event["message"] = redact(event["message"]) if isinstance(event["message"], str) else event["message"]
    le = event.get("logentry")
    if isinstance(le, dict):
        if bare:  # the template only: its values may be local-only text
            event["logentry"] = {"message": redact(le.get("message")), "params": []}
            event.pop("message", None)
        else:
            event["logentry"] = {"message": redact(le.get("message")), "formatted": redact(le.get("formatted")),
                                 "params": [redact(str(p)) for p in le.get("params") or []]}
    exc = event.get("exception")
    for v in (exc.get("values") if isinstance(exc, dict) else None) or []:
        if isinstance(v, dict):
            if bare:
                v.pop("value", None)
            else:
                v["value"] = redact(v.get("value"))
            frames = ((v.get("stacktrace") or {}).get("frames")) or []
            for f in frames:
                if isinstance(f, dict):
                    f.pop("vars", None)
                    if "abs_path" in f:
                        f["abs_path"] = _strip_query(f["abs_path"])
    threads = event.get("threads")
    for t in (threads.get("values") if isinstance(threads, dict) else None) or []:
        for f in ((t.get("stacktrace") or {}).get("frames")) or []:
            if isinstance(f, dict):
                f.pop("vars", None)
    ctx = event.get("contexts")
    if isinstance(ctx, dict):
        for k in ("trace", "response", "state"):
            ctx.pop(k, None)
    tags = event.get("tags")
    if isinstance(tags, dict):
        event["tags"] = {k: redact(v) for k, v in tags.items() if k not in ("url", "transaction")}
    if "transaction" in event:
        event["transaction"] = _strip_query(event["transaction"]) if "://" in str(event["transaction"]) \
            else _redact_path(str(event["transaction"]))
    return event


def init(process: str) -> bool:
    """Start the SDK in this process ("app" or "worker") when MAILR_SENTRY_DSN is set."""
    global _enabled
    s = get_settings()
    if _enabled or not s.sentry_dsn:
        return _enabled
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        log.warning("MAILR_SENTRY_DSN is set but sentry-sdk is not installed")
        return False
    try:
        sentry_sdk.init(
            dsn=s.sentry_dsn,
            release=release(),
            environment=s.sentry_environment or None,
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            max_breadcrumbs=0,
            traces_sample_rate=None,
            in_app_include=["mailr"],
            before_send=scrub,
            integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
        )
    except Exception as e:  # noqa: BLE001  a bad DSN must not stop the app
        log.warning("error reporting off: %s", type(e).__name__)
        return False
    sentry_sdk.set_tag("process", process)
    dsn_service()  # the DSN host's class, looked up now rather than in the first report
    _enabled = True
    log.info("error reporting on (%s)", release() or "no release")
    return True


def capture(exc: BaseException, **tags: Any) -> None:
    """Report an error that was handled (a job that failed for good). No-op while off."""
    if not _enabled:
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        for k, v in tags.items():
            scope.set_tag(k, v)
        sentry_sdk.capture_exception(exc)


def set_user(user_id: int) -> None:
    """The signed-in user's id on the current request's reports (nothing else about them)."""
    if not _enabled:
        return
    import sentry_sdk

    sentry_sdk.get_isolation_scope().set_user({"id": str(user_id)})
