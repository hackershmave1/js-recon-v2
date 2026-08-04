import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KingfisherRule:
    rule_id: str
    name: str
    pattern: str
    compiled_regex: re.Pattern[str]
    confidence: str
    min_entropy: float
    pattern_requirements: dict[str, Any]


class KingfisherRulesLoader:
    _cache: list[KingfisherRule] | None = None
    _cache_lock = Lock()

    def __init__(self, rules_dir: Path | None = None):
        self.rules_dir = rules_dir or Path(__file__).resolve().parent / "rules"

    def load_rules(self) -> list[KingfisherRule]:
        with self._cache_lock:
            if self._cache is not None:
                return self._cache

            rules: list[KingfisherRule] = []
            for file_path in self._rule_files():
                rules.extend(self._load_rules_from_file(file_path))

            logger.info("Loaded %s Kingfisher rules from %s", len(rules), self.rules_dir)
            self._cache = rules
            return rules

    def _rule_files(self) -> list[Path]:
        if not self.rules_dir.exists():
            logger.warning("Kingfisher rules directory not found: %s", self.rules_dir)
            return []

        manifest_path = self.rules_dir / "_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                files = manifest.get("files", [])
                ordered = [self.rules_dir / file_name for file_name in files]
                return [path for path in ordered if path.exists() and path.is_file()]
            except Exception as exc:
                logger.warning("Failed to parse rules manifest %s: %s", manifest_path, exc)

        return sorted(self.rules_dir.glob("*.yaml"))

    def _load_rules_from_file(self, file_path: Path) -> list[KingfisherRule]:
        try:
            payload = yaml.safe_load(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse Kingfisher rule file %s: %s", file_path.name, exc)
            return []

        raw_rules: list[dict[str, Any]]
        if isinstance(payload, dict):
            raw_rules = payload.get("rules", []) or []
        elif isinstance(payload, list):
            raw_rules = payload
        else:
            raw_rules = []

        compiled: list[KingfisherRule] = []
        for raw in raw_rules:
            rule = self._compile_rule(raw)
            if rule:
                compiled.append(rule)
        return compiled

    def _compile_rule(self, raw_rule: dict[str, Any]) -> KingfisherRule | None:
        if not isinstance(raw_rule, dict):
            return None

        pattern = (raw_rule.get("pattern") or "").strip("\n")
        if not pattern:
            return None

        rule_id = str(raw_rule.get("id") or raw_rule.get("name") or "kingfisher.unknown")
        name = str(raw_rule.get("name") or rule_id)
        confidence = str(raw_rule.get("confidence") or "medium").lower()
        min_entropy = self._to_float(raw_rule.get("min_entropy"), 0.0)

        requirements = raw_rule.get("pattern_requirements")
        if not isinstance(requirements, dict):
            requirements = {}

        try:
            compiled_regex = re.compile(pattern, re.MULTILINE)
        except re.error as exc:
            logger.debug("Skipping Kingfisher rule %s due to regex error: %s", rule_id, exc)
            return None

        return KingfisherRule(
            rule_id=rule_id,
            name=name,
            pattern=pattern,
            compiled_regex=compiled_regex,
            confidence=confidence,
            min_entropy=min_entropy,
            pattern_requirements=requirements,
        )

    def _to_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default


def calculate_entropy(value: str) -> float:
    if not value:
        return 0.0

    import math

    frequencies: dict[str, int] = {}
    for char in value:
        frequencies[char] = frequencies.get(char, 0) + 1

    length = len(value)
    entropy = 0.0
    for count in frequencies.values():
        probability = count / length
        entropy -= probability * math.log2(probability)

    return entropy


def validate_pattern_requirements(match_value: str, requirements: dict[str, Any] | None) -> bool:
    if not requirements:
        return True

    min_digits = _to_int(requirements.get("min_digits"))
    if min_digits is not None and _count_regex(match_value, r"\d") < min_digits:
        return False

    min_uppercase = _to_int(requirements.get("min_uppercase"))
    if min_uppercase is not None and _count_regex(match_value, r"[A-Z]") < min_uppercase:
        return False

    min_lowercase = _to_int(requirements.get("min_lowercase"))
    if min_lowercase is not None and _count_regex(match_value, r"[a-z]") < min_lowercase:
        return False

    min_special = _to_int(requirements.get("min_special_chars"))
    if min_special is not None:
        special_chars = requirements.get("special_chars") or "!@#$%^&*()_+-=[]{}|;:'\",.<>?/\\`~"
        escaped = re.escape(str(special_chars))
        if _count_regex(match_value, rf"[{escaped}]") < min_special:
            return False

    ignore_terms = requirements.get("ignore_if_contains")
    if isinstance(ignore_terms, list):
        lower_value = match_value.lower()
        for term in ignore_terms:
            candidate = str(term).strip().lower()
            if candidate and candidate in lower_value:
                return False

    return True


def _count_regex(value: str, pattern: str) -> int:
    return len(re.findall(pattern, value))


def _to_int(value: Any) -> int | None:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else None
    except Exception:
        return None
