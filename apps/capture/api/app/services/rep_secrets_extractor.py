import logging
import re
from typing import Any

from .kingfisher_rules_loader import (
    KingfisherRulesLoader,
    calculate_entropy,
    validate_pattern_requirements,
)
from .regex_utils import chunked_regex

logger = logging.getLogger(__name__)

KNOWN_FALSE_POSITIVE_PATTERNS = [
    re.compile(r"^[a-f0-9]{40}$", re.IGNORECASE),
    re.compile(r"^[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+$"),
    re.compile(r"^[a-z][a-zA-Z0-9]+(?:[A-Z][a-z0-9]+)+$"),
    re.compile(r"^(?:map|filter|reduce|forEach|slice|splice|concat)", re.IGNORECASE),
    re.compile(r"^_react|_emotion|_styled|_next", re.IGNORECASE),
    re.compile(r"sourceMappingURL", re.IGNORECASE),
    re.compile(r"^__webpack", re.IGNORECASE),
    re.compile(r"^module\.", re.IGNORECASE),
    re.compile(r"^exports\.", re.IGNORECASE),
]

FALSE_POSITIVE_CONTEXT_PATTERNS = [
    re.compile(r"base64,", re.IGNORECASE),
    re.compile(r"data:image", re.IGNORECASE),
    re.compile(r";base64", re.IGNORECASE),
    re.compile(r'"(?:publicKey|privateKey|data|content|image|icon|font|logo|avatar|thumbnail|media|src|href)"\s*:', re.IGNORECASE),
    re.compile(r"iVBOR|AAAA|/png|/jpeg|/jpg|/gif|/webp|/svg", re.IGNORECASE),
    re.compile(r"sourceMappingURL=", re.IGNORECASE),
    re.compile(r"webpack://", re.IGNORECASE),
    re.compile(r"__webpack", re.IGNORECASE),
    re.compile(r"\.chunk\.js", re.IGNORECASE),
    re.compile(r"/\*#\s*source", re.IGNORECASE),
    re.compile(r"import\s+.*\s+from\s+['\"]", re.IGNORECASE),
    re.compile(r"require\s*\(['\"]", re.IGNORECASE),
    re.compile(r'["\']data["\']\s*:', re.IGNORECASE),
    re.compile(r'["\']image["\']\s*:', re.IGNORECASE),
    re.compile(r"// data:image", re.IGNORECASE),
]


class RepSecretsExtractor:
    def __init__(self, rules_loader: KingfisherRulesLoader | None = None):
        self.rules_loader = rules_loader or KingfisherRulesLoader()

    def extract(self, content: str, source_file: str | None = None) -> list[dict[str, Any]]:
        if not content:
            return []

        rules = self.rules_loader.load_rules()
        if not rules:
            return []

        results: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()

        # Check if we should use chunked processing
        use_chunked = chunked_regex.should_chunk(content)
        
        if use_chunked:
            logger.info(f"Using chunked regex processing for large content ({len(content)} chars)")
            results = self._extract_chunked(content, source_file, rules, seen)
        else:
            results = self._extract_standard(content, source_file, rules, seen)

        results.sort(key=lambda item: int(item.get("confidence_score", 0)), reverse=True)
        return results
    
    def _extract_standard(self, content: str, source_file: str | None, rules, seen: set) -> list[dict[str, Any]]:
        """Standard extraction for smaller files"""
        results = []
        
        for rule in rules:
            regex = rule.compiled_regex
            for match in regex.finditer(content):
                raw_match = match.group(0)
                if not raw_match:
                    continue

                matched_value = self._pick_secret_value(match)
                if not matched_value:
                    continue

                if rule.min_entropy > 0:
                    entropy = calculate_entropy(matched_value)
                    if entropy < rule.min_entropy:
                        continue
                else:
                    entropy = calculate_entropy(matched_value)

                if not validate_pattern_requirements(matched_value, rule.pattern_requirements):
                    continue

                if self._is_known_false_positive(matched_value):
                    continue

                context = self._context(content, match.start(), len(raw_match), width=120)
                if self._has_false_positive_context(context):
                    continue

                if self._is_likely_base64_data(matched_value, context):
                    continue

                line_text = self._line_text(content, match.start())
                if self._is_comment_line(line_text):
                    continue

                confidence_score = self._confidence_score(rule.confidence, entropy)
                if confidence_score < 60:
                    continue

                confidence = self._to_confidence_label(confidence_score)
                line, column = self._line_col(content, match.start())

                dedupe_key = (
                    rule.rule_id,
                    matched_value,
                    self._normalize_source_file(source_file),
                )
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

                result = self._process_secret_match(
                    rule, match, matched_value, entropy, content, source_file, seen
                )
                if result:
                    results.append(result)
        
        return results
    
    def _extract_chunked(self, content: str, source_file: str | None, rules, seen: set) -> list[dict[str, Any]]:
        """Chunked extraction for large files"""
        results = []
        
        for rule in rules:
            try:
                regex = rule.compiled_regex
                # Process rule across all chunks
                for chunk_content, start_offset, end_offset in chunked_regex.create_chunks(content):
                    for match in regex.finditer(chunk_content):
                        raw_match = match.group(0)
                        if not raw_match:
                            continue

                        matched_value = self._pick_secret_value(match)
                        if not matched_value:
                            continue

                        # Calculate entropy
                        if rule.min_entropy > 0:
                            entropy = calculate_entropy(matched_value)
                            if entropy < rule.min_entropy:
                                continue
                        else:
                            entropy = calculate_entropy(matched_value)

                        # Adjust match position to global content position
                        global_position = start_offset + match.start()
                        
                        # Create a pseudo-match object with adjusted position for global content
                        class AdjustedMatch:
                            def __init__(self, original_match, global_pos):
                                self._match = original_match
                                self._global_pos = global_pos
                            
                            def start(self):
                                return self._global_pos
                            
                            def group(self, *args):
                                return self._match.group(*args)
                        
                        adjusted_match = AdjustedMatch(match, global_position)
                        
                        result = self._process_secret_match(
                            rule, adjusted_match, matched_value, entropy, content, source_file, seen
                        )
                        if result:
                            results.append(result)
                            
            except Exception as e:
                logger.warning(f"Error processing rule {rule.rule_id} in chunked mode: {e}")
                continue
        
        return results
    
    def _process_secret_match(self, rule, match, matched_value: str, entropy: float, 
                            content: str, source_file: str | None, seen: set) -> dict[str, Any] | None:
        """Process a single secret match and return result dict or None"""
        if not validate_pattern_requirements(matched_value, rule.pattern_requirements):
            return None

        if self._is_known_false_positive(matched_value):
            return None

        context = self._context(content, match.start(), len(matched_value), width=120)
        if self._has_false_positive_context(context):
            return None

        if self._is_likely_base64_data(matched_value, context):
            return None

        line_text = self._line_text(content, match.start())
        if self._is_comment_line(line_text):
            return None

        confidence_score = self._confidence_score(rule.confidence, entropy)
        if confidence_score < 60:
            return None

        confidence = self._to_confidence_label(confidence_score)
        line, column = self._line_col(content, match.start())

        dedupe_key = (
            rule.rule_id,
            matched_value,
            self._normalize_source_file(source_file),
        )
        if dedupe_key in seen:
            return None
        seen.add(dedupe_key)

        return {
            "value": matched_value,
            "match": matched_value,
            "type": rule.name,
            "rule": rule.rule_id,
            "ruleName": rule.name,
            "ruleId": rule.rule_id,
            "confidence": confidence,
            "confidence_score": confidence_score,
            "entropy": f"{entropy:.2f}",
            "extractor": "rep_kingfisher",
            "file": source_file or "unknown",
            "source_file": source_file or "unknown",
            "line": line,
            "column": column,
            "context": context,
        }

    def _pick_secret_value(self, match: re.Match[str]) -> str:
        if match.lastindex:
            for idx in range(1, match.lastindex + 1):
                captured = match.group(idx)
                if captured and captured.strip():
                    return captured.strip()
        return match.group(0).strip()

    def _is_known_false_positive(self, value: str) -> bool:
        return any(pattern.search(value) for pattern in KNOWN_FALSE_POSITIVE_PATTERNS)

    def _has_false_positive_context(self, context: str) -> bool:
        return any(pattern.search(context) for pattern in FALSE_POSITIVE_CONTEXT_PATTERNS)

    def _is_likely_base64_data(self, value: str, context: str) -> bool:
        if re.search(r"data:[\w/-]+;base64,", context):
            return True
        if re.search(r"={1,2}$", value) and len(value) > 100:
            return True
        if len(value) > 200 and re.fullmatch(r"[A-Za-z0-9+/=]+", value):
            return True
        before_context = context[:100]
        if re.search(r'"(?:data|content|image|icon|font|media|src|href|asset|resource)"\s*:\s*"[^"]*$', before_context, re.IGNORECASE):
            return True
        if re.search(r"(?:const|let|var)\s+(?:data|image|icon|font|asset|resource|content)\w*\s*=\s*[\"'`][^\"'`]*$", before_context, re.IGNORECASE):
            return True
        return False

    def _is_comment_line(self, line: str) -> bool:
        trimmed = line.strip()
        return bool(re.match(r"^\s*//", trimmed) or re.match(r"^\s*\*", trimmed) or re.match(r"^\s*/\*", trimmed))

    def _confidence_score(self, base_confidence: str, entropy: float) -> int:
        confidence = 60
        normalized = (base_confidence or "medium").lower()
        if normalized == "high":
            confidence = 85
        elif normalized == "medium":
            confidence = 70

        if entropy > 4.5:
            confidence += 10
        elif entropy < 3.5:
            confidence -= 10

        return max(0, min(100, confidence))

    def _to_confidence_label(self, score: int) -> str:
        if score >= 80:
            return "high"
        if score >= 55:
            return "medium"
        return "low"

    def _line_col(self, content: str, index: int) -> tuple[int, int]:
        line = content.count("\n", 0, index) + 1
        previous_newline = content.rfind("\n", 0, index)
        column = index + 1 if previous_newline == -1 else index - previous_newline
        return line, max(1, column)

    def _line_text(self, content: str, index: int) -> str:
        line_start = content.rfind("\n", 0, index) + 1
        line_end = content.find("\n", index)
        if line_end == -1:
            line_end = len(content)
        return content[line_start:line_end]

    def _context(self, content: str, start: int, length: int, width: int = 100) -> str:
        begin = max(0, start - width)
        end = min(len(content), start + length + width)
        return content[begin:end]

    def _normalize_source_file(self, source_file: str | None) -> str:
        if not source_file:
            return ""
        return source_file.split("?", 1)[0].split("#", 1)[0]
