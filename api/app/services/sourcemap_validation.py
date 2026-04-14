from __future__ import annotations

import re
from datetime import datetime
from typing import Any


COMPLETED_STATUSES = {"completed", "completed_limited"}
PROCESSING_STATUSES = {"pending", "processing", "failed", "completed", "completed_limited"}


def extract_error_class(processing_error: str | None) -> str | None:
    if not processing_error:
        return None
    match = re.match(r"^\[([a-z0-9_]+)\]", str(processing_error).strip(), flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower()


def infer_http_status(error_class: str | None, processing_error: str | None = None) -> int | None:
    if not error_class and not processing_error:
        return None

    error_class = (error_class or "").lower()
    if error_class == "fetch_http_401":
        return 401
    if error_class == "fetch_http_403":
        return 403
    if error_class == "fetch_http_404":
        return 404
    if error_class == "fetch_http_429":
        return 429
    if error_class == "fetch_http_5xx":
        return 500

    if not processing_error:
        return None
    match = re.search(r"(\d{3})", str(processing_error))
    if not match:
        return None
    try:
        code = int(match.group(1))
        if 100 <= code <= 599:
            return code
    except Exception:
        return None
    return None


def normalize_validation_state(raw_state: Any) -> dict[str, Any]:
    if isinstance(raw_state, dict):
        return dict(raw_state)
    return {}


def merge_validation_state(existing_state: Any, updates: dict[str, Any] | None) -> dict[str, Any]:
    state = normalize_validation_state(existing_state)
    if updates:
        for key, value in updates.items():
            state[key] = value
    state["updated_at"] = datetime.utcnow().isoformat()
    return state


def build_initial_validation_state(
    *,
    detected: bool,
    fetched: bool | None = None,
    http_status: int | None = None,
    content_type: str | None = None,
    json_valid: bool | None = None,
    processed: bool = False,
    candidate_source: str | None = None,
    selected_candidate: str | None = None,
    failure_class: str | None = None,
) -> dict[str, Any]:
    return merge_validation_state(
        {},
        {
            "detected": bool(detected),
            "fetched": fetched,
            "http_status": http_status,
            "content_type": content_type,
            "json_valid": json_valid,
            "processed": bool(processed),
            "candidate_source": candidate_source,
            "selected_candidate": selected_candidate,
            "failure_class": failure_class,
        },
    )


def derive_validation_state(source_map: Any) -> dict[str, Any]:
    state = normalize_validation_state(getattr(source_map, "validation_state", None))
    status = str(getattr(source_map, "processing_status", "") or "").lower()
    processing_error = getattr(source_map, "processing_error", None)
    error_class = str(state.get("failure_class") or extract_error_class(processing_error) or "").lower() or None

    detected = state.get("detected")
    if detected is None:
        detected = bool(getattr(source_map, "detected_map_url", None) or getattr(source_map, "map_url", None))
    else:
        detected = bool(detected)

    fetched = state.get("fetched")
    if fetched is None and status in PROCESSING_STATUSES:
        fetched = status != "pending" and detected

    http_status = state.get("http_status")
    if http_status is None:
        http_status = infer_http_status(error_class, processing_error)
    if http_status is not None:
        try:
            http_status = int(http_status)
        except Exception:
            http_status = None

    content_type = state.get("content_type")
    if content_type is not None:
        content_type = str(content_type)

    json_valid = state.get("json_valid")
    if json_valid is None:
        if status in COMPLETED_STATUSES:
            json_valid = True
        elif error_class in {"decode_invalid_json", "decode_content"}:
            json_valid = False

    processed = state.get("processed")
    if processed is None:
        processed = status in COMPLETED_STATUSES
    else:
        processed = bool(processed)

    processed_at = getattr(source_map, "processed_at", None)
    processed_at_iso = processed_at.isoformat() if processed_at else None

    return {
        "detected": detected,
        "fetched": fetched,
        "http_status": http_status,
        "content_type": content_type,
        "json_valid": json_valid,
        "processed": processed,
        "candidate_source": state.get("candidate_source"),
        "selected_candidate": state.get("selected_candidate") or getattr(source_map, "detected_map_url", None) or getattr(source_map, "map_url", None),
        "failure_class": error_class,
        "updated_at": state.get("updated_at") or processed_at_iso,
    }


def summarize_validation(states: list[dict[str, Any]]) -> dict[str, Any]:
    total_js = len(states)
    map_candidates = sum(1 for item in states if item.get("detected"))
    map_fetched = sum(1 for item in states if item.get("fetched"))
    json_valid = sum(1 for item in states if item.get("json_valid") is True)
    processed = sum(1 for item in states if item.get("processed"))
    failed = sum(1 for item in states if item.get("failure_class"))
    no_map = max(0, total_js - map_candidates)

    reason_counts: dict[str, int] = {}
    for item in states:
        reason = item.get("failure_class")
        if not reason:
            continue
        reason_key = str(reason)
        reason_counts[reason_key] = reason_counts.get(reason_key, 0) + 1

    def pct(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)

    return {
        "denominators": {
            "total_js": total_js,
            "map_candidates": map_candidates,
            "map_fetched": map_fetched,
            "json_checked": map_fetched,
        },
        "counts": {
            "no_map_candidate": no_map,
            "processed": processed,
            "failed": failed,
            "json_valid": json_valid,
        },
        "rates": {
            "candidatePctOfJs": pct(map_candidates, total_js),
            "fetchPctOfCandidates": pct(map_fetched, map_candidates),
            "processPctOfCandidates": pct(processed, map_candidates),
            "jsonValidPctOfFetched": pct(json_valid, map_fetched),
        },
        "failure_reasons": reason_counts,
    }
