import logging
import re
from datetime import datetime
from typing import Any, Dict, List

from .async_utils import run_coroutine_sync
from .jsluice_extractor import JSluiceExtractor
from .native_sourcemap_processor import NativeSourceMapProcessor
from .parameter_extractor import ParameterExtractor
from .rep_endpoints_extractor import RepEndpointsExtractor
from .rep_secrets_extractor import RepSecretsExtractor
from .sensitive_file_detector import SensitiveFileDetector

logger = logging.getLogger(__name__)


class ComprehensiveExtractor:
    """
    Unified extractor combining REP-style extractors, optional jsluice,
    sourcemap reconstruction, and dependency analysis.
    """

    def __init__(self):
        self.rep_endpoints = RepEndpointsExtractor()
        self.rep_secrets = RepSecretsExtractor()
        self.sensitive_file_detector = SensitiveFileDetector()
        self.parameter_extractor = ParameterExtractor()

        try:
            self.jsluice = JSluiceExtractor()
        except (FileNotFoundError, PermissionError) as exc:
            logger.warning("jsluice not available: %s", exc)
            self.jsluice = None

        try:
            self.sourcemapper = NativeSourceMapProcessor()
        except Exception as exc:
            logger.warning("sourcemapper not available: %s", exc)
            self.sourcemapper = None

    def extract_all(self, js_content: str, metadata: Dict[str, Any], options: Dict[str, Any] | None = None) -> Dict[str, Any]:
        analysis_start = datetime.utcnow()
        options = options or {}
        extractor_options = self._resolve_extractor_options(options)

        js_url = metadata.get("url", "unknown")
        include_sourcemap = bool(options.get("include_sourcemap", True))
        resolve_urls = bool(options.get("resolve_urls", True))

        analysis = {
            "endpoints": [],
            "secrets": [],
            "dependencies": [],
            "sensitive_files": [],
            "params": [],
            "sourcemap": None,
            "reconstructed_files": [],
        }
        extractors_used: list[str] = []

        if extractor_options["use_rep_endpoints"]:
            try:
                rep_endpoints = self.rep_endpoints.extract(js_content, js_url)
                analysis["endpoints"].extend(
                    self._normalize_endpoint_records(rep_endpoints, source_file=js_url, extractor="rep_endpoint_extractor")
                )
                extractors_used.append("rep_endpoint_extractor")
            except Exception as exc:
                logger.error("REP endpoint extraction failed: %s", exc)

        if extractor_options["use_rep_secrets"]:
            try:
                rep_secrets = self.rep_secrets.extract(js_content, js_url)
                analysis["secrets"].extend(
                    self._normalize_secret_records(rep_secrets, source_file=js_url, extractor="rep_kingfisher")
                )
                extractors_used.append("rep_kingfisher")
            except Exception as exc:
                logger.error("REP secret extraction failed: %s", exc)

        if extractor_options["use_jsluice_endpoints"] and self.jsluice:
            try:
                raw_urls = self.jsluice.extract_urls(js_content, js_url, resolve_urls=resolve_urls)
                analysis["endpoints"].extend(
                    self._normalize_endpoint_records(raw_urls, source_file=js_url, extractor="jsluice_urls")
                )
                extractors_used.append("jsluice_urls")
            except Exception as exc:
                logger.error("jsluice URL extraction failed: %s", exc)

        if extractor_options["use_jsluice_secrets"] and self.jsluice:
            try:
                raw_secrets = self.jsluice.extract_secrets(js_content)
                analysis["secrets"].extend(
                    self._normalize_secret_records(raw_secrets, source_file=js_url, extractor="jsluice_secrets")
                )
                extractors_used.append("jsluice_secrets")
            except Exception as exc:
                logger.error("jsluice secret extraction failed: %s", exc)

        try:
            dependencies = self._extract_dependencies(js_content, js_url)
            analysis["dependencies"] = dependencies
            extractors_used.append("dependency_parser")
        except Exception as exc:
            logger.error("Dependency extraction failed: %s", exc)

        # Detect sensitive file references
        if extractor_options["use_sensitive_file_detection"]:
            try:
                include_low_conf = bool(options.get("include_low_confidence_files", False))
                sensitive_files = self.sensitive_file_detector.detect_sensitive_files(
                    js_content, js_url, include_low_confidence=include_low_conf
                )
                analysis["sensitive_files"] = sensitive_files
                if sensitive_files:
                    extractors_used.append("sensitive_file_detector")
            except Exception as exc:
                logger.error("Sensitive file detection failed: %s", exc)

        # Extract parameter signals
        if extractor_options["use_parameter_extraction"]:
            try:
                params = self.parameter_extractor.extract(
                    js_content, 
                    source_file=js_url,
                    content_type="javascript"
                )
                analysis["params"] = self._normalize_parameter_records(params, source_file=js_url, extractor="parameter_extractor")
                if params:
                    extractors_used.append("parameter_extractor")
            except Exception as exc:
                logger.error("Parameter extraction failed: %s", exc)

        if include_sourcemap and self.sourcemapper:
            sourcemap_url = metadata.get("sourceMapUrl")
            if not sourcemap_url:
                sourcemap_url = self.sourcemapper._extract_sourcemap_url_from_content(js_content, js_url)

            if sourcemap_url:
                try:
                    sourcemap_result = run_coroutine_sync(
                        self.sourcemapper.process_sourcemap_from_url(js_url, sourcemap_url)
                    )
                    analysis["sourcemap"] = sourcemap_result

                    if sourcemap_result.get("success"):
                        reconstructed_files = sourcemap_result.get("files", [])
                        analysis["reconstructed_files"] = reconstructed_files
                        extractors_used.append("sourcemapper")

                        additional = self._analyze_reconstructed_files(reconstructed_files, extractor_options)
                        analysis["endpoints"].extend(
                            self._normalize_endpoint_records(
                                additional.get("endpoints", []),
                                source_file=js_url,
                                extractor="reconstructed",
                            )
                        )
                        analysis["secrets"].extend(
                            self._normalize_secret_records(
                                additional.get("secrets", []),
                                source_file=js_url,
                                extractor="reconstructed",
                            )
                        )
                        analysis["params"].extend(
                            self._normalize_parameter_records(
                                additional.get("params", []),
                                source_file=js_url,
                                extractor="reconstructed",
                            )
                        )
                except Exception as exc:
                    logger.error("Source map processing failed: %s", exc)

        if extractor_options["use_custom_patterns"]:
            try:
                custom_patterns = self._extract_custom_patterns(js_content, js_url)
                analysis["endpoints"].extend(
                    self._normalize_endpoint_records(
                        custom_patterns.get("endpoints", []),
                        source_file=js_url,
                        extractor="custom_patterns",
                    )
                )
                analysis["secrets"].extend(
                    self._normalize_secret_records(
                        custom_patterns.get("secrets", []),
                        source_file=js_url,
                        extractor="custom_patterns",
                    )
                )
                extractors_used.append("custom_patterns")
            except Exception as exc:
                logger.error("Custom pattern extraction failed: %s", exc)

        analysis = self._deduplicate_results(analysis)

        processing_time_ms = int((datetime.utcnow() - analysis_start).total_seconds() * 1000)
        stats = {
            "total_endpoints": len(analysis["endpoints"]),
            "total_secrets": len(analysis["secrets"]),
            "total_dependencies": len(analysis["dependencies"]),
            "total_sensitive_files": len(analysis["sensitive_files"]),
            "total_params": len(analysis["params"]),
            "total_reconstructed_files": len(analysis["reconstructed_files"]),
            "processing_time_ms": processing_time_ms,
            "extractor_options": extractor_options,
        }

        return {
            "success": True,
            "metadata": metadata,
            "analysis": analysis,
            "extractors_used": sorted(set(extractors_used)),
            "stats": stats,
            "timestamp": analysis_start.isoformat(),
        }

    def _resolve_extractor_options(self, options: Dict[str, Any]) -> Dict[str, bool]:
        use_jsluice = options.get("use_jsluice")
        default_jsluice = bool(use_jsluice) if use_jsluice is not None else False

        return {
            "use_rep_endpoints": bool(options.get("use_rep_endpoints", True)),
            "use_rep_secrets": bool(options.get("use_rep_secrets", True)),
            "use_jsluice_endpoints": bool(options.get("use_jsluice_endpoints", default_jsluice)),
            "use_jsluice_secrets": bool(options.get("use_jsluice_secrets", default_jsluice)),
            "use_custom_patterns": bool(options.get("use_custom_patterns", False)),
            "use_sensitive_file_detection": bool(options.get("use_sensitive_file_detection", True)),
            "use_parameter_extraction": bool(options.get("use_parameter_extraction", True)),
        }

    def _extract_dependencies(self, js_content: str, base_url: str) -> List[Dict[str, Any]]:
        dependencies = []
        patterns = [
            (r'import\s+.*?\s+from\s+[\'"`]([^\'"`]+)[\'"`]', "es6_import"),
            (r'import\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)', "dynamic_import"),
            (r'require\s*\(\s*[\'"`]([^\'"`]+)[\'"`]\s*\)', "commonjs_require"),
            (r'require\s*\(\s*\[[^\]]*[\'"`]([^\'"`]+)[\'"`]', "amd_require"),
            (r'__webpack_require__\s*\(\s*(\d+)\s*\)', "webpack_chunk"),
        ]

        for pattern, dep_type in patterns:
            for match in re.finditer(pattern, js_content):
                dep_path = match.group(1)
                dependencies.append(
                    {
                        "path": dep_path,
                        "type": dep_type,
                        "line": js_content[: match.start()].count("\n") + 1,
                        "resolved_url": self._resolve_dependency_url(dep_path, base_url),
                    }
                )
        return dependencies

    def _resolve_dependency_url(self, dep_path: str, base_url: str) -> str:
        if dep_path.startswith(("http://", "https://")):
            return dep_path
        if dep_path.startswith("/"):
            from urllib.parse import urlparse

            parsed = urlparse(base_url)
            return f"{parsed.scheme}://{parsed.netloc}{dep_path}"
        if dep_path.startswith("./") or dep_path.startswith("../"):
            from urllib.parse import urljoin

            base_dir = base_url.rsplit("/", 1)[0] + "/"
            return urljoin(base_dir, dep_path)
        return f"{base_url.rsplit('/', 1)[0]}/node_modules/{dep_path}"

    def _extract_custom_patterns(self, js_content: str, source_file: str) -> Dict[str, List[Dict[str, Any]]]:
        endpoints: list[dict[str, Any]] = []
        secrets: list[dict[str, Any]] = []

        endpoint_patterns = [
            (r'fetch\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', "fetch_call"),
            (r'axios\.(?:get|post|put|delete|patch)\s*\(\s*[\'"`]([^\'"`]+)[\'"`]', "axios_call"),
            (r'XMLHttpRequest.*?open\s*\(\s*[\'"`][^\'"`]+[\'"`]\s*,\s*[\'"`]([^\'"`]+)[\'"`]', "xhr_call"),
            (r'[\'"`](/api/[^\'"`]+)[\'"`]', "api_path"),
            (r'[\'"`](https?://[^\'"`]+)[\'"`]', "absolute_url"),
        ]
        for pattern, endpoint_type in endpoint_patterns:
            for match in re.finditer(pattern, js_content, re.IGNORECASE):
                endpoints.append(
                    {
                        "url": match.group(1),
                        "type": endpoint_type,
                        "line": js_content[: match.start()].count("\n") + 1,
                        "context": js_content[max(0, match.start() - 50) : match.end() + 50],
                        "confidence": "medium",
                        "extractor": "custom_patterns",
                        "source_file": source_file,
                    }
                )

        secret_patterns = [
            (r'[\'"`]([A-Za-z0-9_-]{32,})[\'"`]', "potential_api_key"),
            (r'password\s*[:=]\s*[\'"`]([^\'"`]+)[\'"`]', "password"),
            (r'api[_-]?key\s*[:=]\s*[\'"`]([^\'"`]+)[\'"`]', "api_key"),
            (r'secret\s*[:=]\s*[\'"`]([^\'"`]+)[\'"`]', "secret"),
            (r'token\s*[:=]\s*[\'"`]([^\'"`]+)[\'"`]', "token"),
        ]
        for pattern, secret_type in secret_patterns:
            for match in re.finditer(pattern, js_content, re.IGNORECASE):
                value = match.group(1)
                if len(value) > 8:
                    secrets.append(
                        {
                            "value": value,
                            "type": secret_type,
                            "line": js_content[: match.start()].count("\n") + 1,
                            "context": js_content[max(0, match.start() - 30) : match.end() + 30],
                            "confidence": "medium",
                            "extractor": "custom_patterns",
                            "source_file": source_file,
                        }
                    )
        return {"endpoints": endpoints, "secrets": secrets}

    def _analyze_reconstructed_files(self, files: List[Dict[str, Any]], options: Dict[str, bool]) -> Dict[str, List[Dict[str, Any]]]:
        additional_analysis = {"endpoints": [], "secrets": [], "params": []}

        for file_info in files:
            if file_info.get("type") != "javascript":
                continue

            content = file_info.get("content", "")
            source_file = file_info.get("path", "reconstructed.js")
            if not content:
                continue

            try:
                if options.get("use_rep_endpoints", True):
                    endpoints = self.rep_endpoints.extract(content, source_file)
                    additional_analysis["endpoints"].extend(endpoints)

                if options.get("use_rep_secrets", True):
                    secrets = self.rep_secrets.extract(content, source_file)
                    additional_analysis["secrets"].extend(secrets)

                if options.get("use_jsluice_endpoints", False) and self.jsluice:
                    endpoints = self.jsluice.extract_urls(content, source_file, resolve_urls=False)
                    for item in endpoints:
                        item.setdefault("source_file", source_file)
                    additional_analysis["endpoints"].extend(endpoints)

                if options.get("use_jsluice_secrets", False) and self.jsluice:
                    secrets = self.jsluice.extract_secrets(content)
                    for item in secrets:
                        item.setdefault("source_file", source_file)
                    additional_analysis["secrets"].extend(secrets)

                if options.get("use_parameter_extraction", True):
                    params = self.parameter_extractor.extract(content, source_file, "javascript")
                    additional_analysis["params"].extend(params)
            except Exception as exc:
                logger.warning("Failed to analyze reconstructed file %s: %s", source_file, exc)

        return additional_analysis

    def _normalize_endpoint_records(self, records: List[Dict[str, Any]], source_file: str, extractor: str) -> List[Dict[str, Any]]:
        normalized = []
        for record in records:
            url = (record.get("url") or record.get("endpoint") or "").strip()
            if not url:
                continue

            line = self._to_int(record.get("line"))
            column = self._to_int(record.get("column"))
            file_name = record.get("source_file") or record.get("file") or source_file
            url = self._canonicalize_endpoint_url(url, file_name) or url
            tool = record.get("extractor") or extractor
            occurrence = self._build_occurrence(file_name, line, column, tool, record.get("context"))

            normalized.append(
                {
                    "url": url,
                    "method": record.get("method"),
                    "type": record.get("type", "endpoint"),
                    "patternType": record.get("patternType") or record.get("pattern_type"),
                    "confidence": record.get("confidence", "medium"),
                    "confidence_score": self._to_int(record.get("confidence_score")),
                    "extractor": tool,
                    "extractors": [tool],
                    "file": file_name,
                    "line": line,
                    "column": column,
                    "context": record.get("context", ""),
                    "occurrences": [occurrence],
                    "occurrenceCount": 1,
                }
            )
        return normalized

    def _normalize_parameter_records(self, records: List[Dict[str, Any]], source_file: str, extractor: str) -> List[Dict[str, Any]]:
        """Normalize parameter extraction records to standard format."""
        normalized = []
        for record in records:
            name = (record.get("name") or "").strip()
            if not name:
                continue
            
            line = self._to_int(record.get("line"))
            column = self._to_int(record.get("column"))
            file_name = record.get("file") or source_file
            tool = record.get("extractor") or extractor
            occurrence = self._build_occurrence(file_name, line, column, tool, record.get("context"))
            
            normalized.append({
                "name": name,
                "source": record.get("source", "unknown"),
                "pattern": record.get("pattern", "unknown"),
                "confidence": record.get("confidence", 0.5),
                "type": "parameter",
                "extractor": tool,
                "extractors": [tool],
                "file": file_name,
                "line": line,
                "column": column,
                "context": record.get("context", ""),
                "occurrences": [occurrence],
                "occurrenceCount": 1,
            })
        
        return normalized

    def _canonicalize_endpoint_url(self, url: str, base_file: str) -> str | None:
        if not url:
            return None

        stripped = url.strip()
        if stripped.startswith(("http://", "https://")):
            return stripped
        if stripped.startswith("//"):
            return f"https:{stripped}"
        if stripped.startswith(("/", "./", "../")):
            try:
                from urllib.parse import urljoin

                return urljoin(base_file, stripped)
            except Exception:
                return stripped
        return stripped

    def _normalize_secret_records(self, records: List[Dict[str, Any]], source_file: str, extractor: str) -> List[Dict[str, Any]]:
        normalized = []
        for record in records:
            value = (record.get("value") or record.get("match") or "").strip()
            if not value:
                continue

            line = self._to_int(record.get("line"))
            column = self._to_int(record.get("column"))
            file_name = record.get("source_file") or record.get("file") or source_file
            tool = record.get("extractor") or extractor
            occurrence = self._build_occurrence(file_name, line, column, tool, record.get("context"))

            normalized.append(
                {
                    "value": value,
                    "type": record.get("type", "secret"),
                    "rule": record.get("rule") or record.get("ruleId"),
                    "ruleName": record.get("ruleName"),
                    "ruleId": record.get("ruleId") or record.get("rule"),
                    "entropy": record.get("entropy"),
                    "confidence": record.get("confidence", "medium"),
                    "confidence_score": self._to_int(record.get("confidence_score")),
                    "extractor": tool,
                    "extractors": [tool],
                    "file": file_name,
                    "line": line,
                    "column": column,
                    "context": record.get("context", ""),
                    "occurrences": [occurrence],
                    "occurrenceCount": 1,
                }
            )
        return normalized

    def _build_occurrence(
        self,
        file_name: str,
        line: int | None,
        column: int | None,
        extractor: str,
        context: str | None,
    ) -> Dict[str, Any]:
        return {
            "file": file_name,
            "line": line,
            "column": column,
            "extractor": extractor,
            "context": context or "",
        }

    def _deduplicate_results(self, analysis: Dict[str, Any]) -> Dict[str, Any]:
        endpoint_map: dict[str, dict[str, Any]] = {}
        for endpoint in analysis.get("endpoints", []):
            key = endpoint.get("url", "").strip()
            if not key:
                continue
            if key not in endpoint_map:
                endpoint_map[key] = endpoint
                continue
            endpoint_map[key] = self._merge_entry(endpoint_map[key], endpoint, value_key="url")

        secret_map: dict[str, dict[str, Any]] = {}
        for secret in analysis.get("secrets", []):
            key = secret.get("value", "").strip()
            if not key:
                continue
            if key not in secret_map:
                secret_map[key] = secret
                continue
            secret_map[key] = self._merge_entry(secret_map[key], secret, value_key="value")

        # Deduplicate sensitive files by path
        sensitive_file_map: dict[str, dict[str, Any]] = {}
        for file_ref in analysis.get("sensitive_files", []):
            key = file_ref.get("path", "").strip()
            if not key:
                continue
            if key not in sensitive_file_map:
                sensitive_file_map[key] = file_ref
                continue
            # For sensitive files, keep the highest confidence detection
            existing_conf = sensitive_file_map[key].get("confidence", "low")
            incoming_conf = file_ref.get("confidence", "low")
            confidence_order = {"high": 2, "medium": 1, "low": 0}
            if confidence_order.get(incoming_conf, 0) > confidence_order.get(existing_conf, 0):
                sensitive_file_map[key] = file_ref

        # Deduplicate parameters by name
        param_map: dict[str, dict[str, Any]] = {}
        for param in analysis.get("params", []):
            key = param.get("name", "").strip()
            if not key:
                continue
            if key not in param_map:
                param_map[key] = param
                continue
            param_map[key] = self._merge_entry(param_map[key], param, value_key="name")

        analysis["endpoints"] = sorted(endpoint_map.values(), key=lambda endpoint: endpoint.get("url", ""))
        analysis["secrets"] = sorted(secret_map.values(), key=lambda secret: secret.get("value", ""))
        analysis["params"] = sorted(param_map.values(), key=lambda param: param.get("name", ""))
        
        # Sort sensitive files by confidence (high first) then by path
        sensitive_files = list(sensitive_file_map.values())
        confidence_order = {"high": 0, "medium": 1, "low": 2}
        analysis["sensitive_files"] = sorted(
            sensitive_files, 
            key=lambda sf: (confidence_order.get(sf.get("confidence", "low"), 2), sf.get("path", ""))
        )
        
        return analysis

    def _merge_entry(self, current: Dict[str, Any], incoming: Dict[str, Any], value_key: str) -> Dict[str, Any]:
        merged = dict(current)

        merged_extractors = set(merged.get("extractors", []))
        merged_extractors.update(incoming.get("extractors", []))
        if incoming.get("extractor"):
            merged_extractors.add(incoming["extractor"])
        merged["extractors"] = sorted(merged_extractors)
        merged["extractor"] = merged["extractors"][0] if len(merged["extractors"]) == 1 else "multiple"

        merged_occurrences = list(merged.get("occurrences", []))
        seen = {
            (occ.get("file"), occ.get("line"), occ.get("column"), occ.get("extractor"))
            for occ in merged_occurrences
        }
        for occurrence in incoming.get("occurrences", []):
            key = (occurrence.get("file"), occurrence.get("line"), occurrence.get("column"), occurrence.get("extractor"))
            if key not in seen:
                merged_occurrences.append(occurrence)
                seen.add(key)
        merged["occurrences"] = merged_occurrences
        merged["occurrenceCount"] = len(merged_occurrences)

        if not merged.get("line") and incoming.get("line"):
            merged["line"] = incoming.get("line")
            merged["file"] = incoming.get("file")
            merged["column"] = incoming.get("column")

        if not merged.get("context") and incoming.get("context"):
            merged["context"] = incoming.get("context")

        merged[value_key] = current.get(value_key) or incoming.get(value_key)
        merged["confidence"] = self._max_confidence(current.get("confidence"), incoming.get("confidence"))

        if not merged.get("confidence_score") and incoming.get("confidence_score"):
            merged["confidence_score"] = incoming.get("confidence_score")

        for passthrough in ("method", "rule", "ruleName", "ruleId", "entropy", "patternType"):
            if not merged.get(passthrough) and incoming.get(passthrough):
                merged[passthrough] = incoming.get(passthrough)

        return merged

    def _max_confidence(self, first: str | None, second: str | None) -> str:
        order = {"low": 1, "medium": 2, "high": 3}
        first_value = order.get((first or "low").lower(), 1)
        second_value = order.get((second or "low").lower(), 1)
        highest = max(first_value, second_value)
        if highest == 3:
            return "high"
        if highest == 2:
            return "medium"
        return "low"

    def _to_int(self, value: Any) -> int | None:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else None
        except Exception:
            return None
