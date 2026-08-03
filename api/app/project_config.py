"""Project config schema + inherit/override resolution (pure helpers).

A project owns a ``defaults`` document with four groups (scope/capture/denylist/
analysis). A session resolves its effective config once, at creation, from the
project defaults plus a sparse set of per-field overrides: null = inherit, set =
replace (per leaf; list values are replaced, never unioned). These pure helpers
are the single source of truth for the shape and the merge. No DB, stdlib only."""
import copy
from typing import Any

_OUT_OF_SCOPE_MODES = {"tag", "mute", "exclude"}

SYSTEM_DEFAULTS: dict[str, Any] = {
    "scope": {"rootDomains": [], "includeSubdomains": True},
    "capture": {"outOfScopeMode": "tag", "maxAssetMb": 10},
    "denylist": {"rules": [], "useDefaultProfile": True},
    "analysis": {"analyzeOnUpload": False, "captureSourceMaps": True},
}

# Every leaf a project owns and a session may override, grouped by section.
CONFIG_SCHEMA: dict[str, tuple[str, ...]] = {
    "scope": ("rootDomains", "includeSubdomains"),
    "capture": ("outOfScopeMode", "maxAssetMb"),
    "denylist": ("rules", "useDefaultProfile"),
    "analysis": ("analyzeOnUpload", "captureSourceMaps"),
}


def system_defaults() -> dict[str, Any]:
    return copy.deepcopy(SYSTEM_DEFAULTS)


def deep_merge(base: dict, patch: dict) -> dict:
    """Recursively merge ``patch`` into a copy of ``base``; patch leaves win.
    Used to apply a partial project-defaults update over the stored document."""
    out = copy.deepcopy(base)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_config(doc: dict, *, partial: bool = False) -> dict:
    """Validate a config document against the schema. With ``partial=True`` only
    the sections that are present are checked (used for per-session captureConfig
    and partial project PATCH). Raises ValueError with a human-readable message."""
    if not isinstance(doc, dict):
        raise ValueError("config must be an object")
    for section in CONFIG_SCHEMA:
        if section not in doc:
            if partial:
                continue
            raise ValueError(f"missing config section: {section}")
        if not isinstance(doc[section], dict):
            raise ValueError(f"config section {section} must be an object")

    if "scope" in doc:
        scope = doc["scope"]
        if "rootDomains" in scope and not isinstance(scope["rootDomains"], list):
            raise ValueError("scope.rootDomains must be a list")
        if "includeSubdomains" in scope and not isinstance(scope["includeSubdomains"], bool):
            raise ValueError("scope.includeSubdomains must be a boolean")
    if "capture" in doc:
        capture = doc["capture"]
        if "outOfScopeMode" in capture and capture["outOfScopeMode"] not in _OUT_OF_SCOPE_MODES:
            raise ValueError("capture.outOfScopeMode must be one of tag|mute|exclude")
        if "maxAssetMb" in capture:
            mb = capture["maxAssetMb"]
            if isinstance(mb, bool) or not isinstance(mb, (int, float)) or mb <= 0 or mb > 10:
                raise ValueError("capture.maxAssetMb must be a number in (0, 10]")
    if "denylist" in doc:
        denylist = doc["denylist"]
        if "rules" in denylist:
            if not isinstance(denylist["rules"], list):
                raise ValueError("denylist.rules must be a list")
            for rule in denylist["rules"]:
                if not isinstance(rule, dict) or "pattern" not in rule:
                    raise ValueError("each denylist rule must be an object with a 'pattern'")
        if "useDefaultProfile" in denylist and not isinstance(denylist["useDefaultProfile"], bool):
            raise ValueError("denylist.useDefaultProfile must be a boolean")
    if "analysis" in doc:
        analysis = doc["analysis"]
        for key in ("analyzeOnUpload", "captureSourceMaps"):
            if key in analysis and not isinstance(analysis[key], bool):
                raise ValueError(f"analysis.{key} must be a boolean")
    return doc


def resolve_effective_config(defaults: dict, overrides: dict | None) -> tuple[dict, list[str]]:
    """Resolve a session's effective config from project defaults + sparse overrides.
    Per leaf: use the override if present, else inherit. Returns (effective, override_keys)
    where override_keys is the sorted dotted paths that were overridden."""
    overrides = overrides or {}
    effective = copy.deepcopy(defaults)
    override_keys: list[str] = []
    for section, keys in CONFIG_SCHEMA.items():
        section_override = overrides.get(section)
        if not isinstance(section_override, dict):
            continue
        for key in keys:
            if key in section_override:
                effective.setdefault(section, {})[key] = copy.deepcopy(section_override[key])
                override_keys.append(f"{section}.{key}")
    return effective, sorted(override_keys)


def split_effective(effective: dict) -> tuple[dict, dict]:
    """Split a resolved config into the scope part (stored in session columns) and
    the capture_config part (capture/denylist/analysis, stored as one JSON column)."""
    scope_section = effective.get("scope") or {}
    scope = {
        "rootDomains": list(scope_section.get("rootDomains") or []),
        "includeSubdomains": bool(scope_section.get("includeSubdomains", True)),
    }
    capture_config = {
        section: copy.deepcopy(effective.get(section) or {})
        for section in ("capture", "denylist", "analysis")
    }
    return scope, capture_config
