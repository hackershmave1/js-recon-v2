from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping
from urllib.parse import urlparse


AUTH_CONTEXT_SCHEMA_VERSION = "1.0"
AUTH_HEADER_ALLOWLIST = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
        "x-xsrf-token",
        "x-access-token",
        "x-session-token",
    }
)
AUTH_CONTEXT_MAX_HEADERS = 12
AUTH_CONTEXT_MAX_HEADER_VALUE_LENGTH = 8192
AUTH_REPLAY_ELIGIBLE_ERROR_CLASSES = frozenset(
    {
        "fetch_http_4xx",
        "fetch_http_401",
        "fetch_http_403",
        "fetch_http_429",
        "fetch_http_5xx",
        "fetch_network",
        "processing_timeout",
    }
)


def _extract_hostname(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").strip().lower()
        return host or None
    except Exception:
        return None


def _normalize_domain(domain: str | None) -> str | None:
    if not isinstance(domain, str):
        return None
    normalized = domain.strip().lower().lstrip(".")
    return normalized or None


def _host_matches_scope(host: str | None, scope: str | None) -> bool:
    host_value = _normalize_domain(host)
    scope_value = _normalize_domain(scope)
    if not host_value or not scope_value:
        return False
    if host_value == scope_value:
        return True
    return host_value.endswith(f".{scope_value}")


def _sanitize_header_value(value: str) -> str:
    sanitized = value.replace("\r", " ").replace("\n", " ").strip()
    if len(sanitized) > AUTH_CONTEXT_MAX_HEADER_VALUE_LENGTH:
        sanitized = sanitized[:AUTH_CONTEXT_MAX_HEADER_VALUE_LENGTH]
    return sanitized


def _sanitize_headers(raw_headers: Any) -> dict[str, str]:
    if not isinstance(raw_headers, Mapping):
        return {}
    sanitized: dict[str, str] = {}
    for raw_name, raw_value in raw_headers.items():
        if len(sanitized) >= AUTH_CONTEXT_MAX_HEADERS:
            break
        if not isinstance(raw_name, str) or raw_value is None:
            continue
        name = raw_name.strip().lower()
        if not name or name not in AUTH_HEADER_ALLOWLIST:
            continue
        value = _sanitize_header_value(str(raw_value))
        if not value:
            continue
        sanitized[name] = value
    return sanitized


def _extract_cookie_names(cookie_header: str) -> list[str]:
    if not isinstance(cookie_header, str) or not cookie_header.strip():
        return []
    cookie_names: list[str] = []
    seen: set[str] = set()
    for segment in cookie_header.split(";"):
        candidate = segment.split("=", 1)[0].strip()
        if not candidate:
            continue
        normalized = candidate.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        cookie_names.append(candidate)
        if len(cookie_names) >= 64:
            break
    return cookie_names


def _sanitize_cookie_metadata(cookie_header: str | None, cookie_payload: Any) -> dict[str, Any]:
    header_cookie_names = _extract_cookie_names(cookie_header or "")
    cookie_names: list[str] = list(header_cookie_names)

    if isinstance(cookie_payload, Mapping):
        names_payload = cookie_payload.get("names")
        if isinstance(names_payload, list):
            from_payload: list[str] = []
            seen: set[str] = set()
            for item in names_payload:
                if not isinstance(item, str):
                    continue
                candidate = item.strip()
                if not candidate:
                    continue
                key = candidate.lower()
                if key in seen:
                    continue
                seen.add(key)
                from_payload.append(candidate)
                if len(from_payload) >= 64:
                    break
            if from_payload:
                cookie_names = from_payload

    cookie_present = bool(cookie_names)
    if isinstance(cookie_payload, Mapping) and "present" in cookie_payload:
        cookie_present = bool(cookie_payload.get("present"))

    count_value: int | None = None
    if isinstance(cookie_payload, Mapping):
        raw_count = cookie_payload.get("count")
        if isinstance(raw_count, int) and raw_count >= 0:
            count_value = raw_count
    if count_value is None:
        count_value = len(cookie_names)

    return {
        "present": cookie_present,
        "names": cookie_names,
        "count": count_value,
    }


def _redact_header_value(header_name: str, header_value: str) -> str:
    name = (header_name or "").lower()
    if name == "authorization":
        scheme = header_value.split(" ", 1)[0].strip() if " " in header_value else "token"
        return f"{scheme} ***"
    if name == "cookie":
        cookie_count = len(_extract_cookie_names(header_value))
        return f"{cookie_count} cookie(s) redacted"
    if len(header_value) <= 8:
        return "***"
    return f"{header_value[:4]}***{header_value[-2:]}"


def _redact_headers(raw_headers: Mapping[str, Any] | Any) -> dict[str, str]:
    headers = _sanitize_headers(raw_headers)
    return {name: _redact_header_value(name, value) for name, value in headers.items()}


def sanitize_captured_auth_context(auth_context: Mapping[str, Any] | None, file_url: str) -> dict[str, Any] | None:
    if not isinstance(auth_context, Mapping):
        return None

    file_host = _extract_hostname(file_url)
    request_url = auth_context.get("requestUrl")
    request_host = _extract_hostname(request_url if isinstance(request_url, str) else None)
    context_domain = _normalize_domain(auth_context.get("domain") if isinstance(auth_context.get("domain"), str) else None)
    effective_domain = context_domain or request_host or file_host

    if not effective_domain or not file_host:
        return None
    if not _host_matches_scope(file_host, effective_domain):
        return None

    replay_headers = _sanitize_headers(auth_context.get("headers"))
    cookie_metadata = _sanitize_cookie_metadata(replay_headers.get("cookie"), auth_context.get("cookie"))
    if not replay_headers and not cookie_metadata["present"]:
        return None

    captured_at = auth_context.get("capturedAt")
    if isinstance(captured_at, str) and captured_at.strip():
        captured_at_value = captured_at.strip()
    else:
        captured_at_value = datetime.now(timezone.utc).isoformat()

    source = auth_context.get("source")
    source_value = source.strip() if isinstance(source, str) and source.strip() else "extension.webRequest"

    request_url_value = request_url.strip() if isinstance(request_url, str) and request_url.strip() else file_url

    return {
        "schemaVersion": AUTH_CONTEXT_SCHEMA_VERSION,
        "capturedAt": captured_at_value,
        "source": source_value,
        "domain": effective_domain,
        "requestUrl": request_url_value,
        "headerAllowlist": sorted(AUTH_HEADER_ALLOWLIST),
        "replayHeaders": replay_headers,
        "headers": _redact_headers(replay_headers),
        "cookie": cookie_metadata,
    }


def get_auth_replay_headers(auth_context: Mapping[str, Any] | None, target_url: str) -> dict[str, str] | None:
    if not isinstance(auth_context, Mapping):
        return None

    target_host = _extract_hostname(target_url)
    context_domain = _normalize_domain(auth_context.get("domain") if isinstance(auth_context.get("domain"), str) else None)
    if not target_host or not context_domain or not _host_matches_scope(target_host, context_domain):
        return None

    replay_headers = _sanitize_headers(auth_context.get("replayHeaders"))
    if not replay_headers:
        replay_headers = _sanitize_headers(auth_context.get("headers"))
    return replay_headers or None


def redact_auth_context_for_output(auth_context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(auth_context, Mapping):
        return None

    result = dict(auth_context)
    replay_headers = result.pop("replayHeaders", None)
    source_headers = replay_headers if isinstance(replay_headers, Mapping) else result.get("headers")
    result["headers"] = _redact_headers(source_headers)

    cookie_value = None
    if isinstance(source_headers, Mapping):
        cookie_raw = source_headers.get("cookie")
        if isinstance(cookie_raw, str):
            cookie_value = cookie_raw
    result["cookie"] = _sanitize_cookie_metadata(cookie_value, result.get("cookie"))
    return result


def redact_file_metadata_for_output(metadata: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None

    result = deepcopy(dict(metadata))
    auth_context = result.get("authContext")
    redacted_auth_context = redact_auth_context_for_output(auth_context if isinstance(auth_context, Mapping) else None)
    if redacted_auth_context:
        result["authContext"] = redacted_auth_context
    elif "authContext" in result:
        result.pop("authContext")
    return result
