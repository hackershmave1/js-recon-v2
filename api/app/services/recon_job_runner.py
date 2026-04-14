from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from sqlalchemy.orm import Session

from ..api.routes.ingestion import FileIn, IngestionPayload, save_files
from ..services.http_fetcher import robust_fetcher
from ..services.native_sourcemap_processor import NativeSourceMapProcessor

logger = logging.getLogger(__name__)
MISS_REASON_TAXONOMY = (
    "not_seen",
    "fetch_4xx",
    "fetch_5xx",
    "fetch_timeout",
    "non_js_content",
    "blocked_by_scope",
    "parse_failed",
    "dedup_skipped",
)

SCRIPT_SRC_REGEX = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)
INLINE_SCRIPT_REGEX = re.compile(r"<script[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
ELEMENT_SCRIPT_REGEX = re.compile(r'["\']([^"\']+\.js(?:\?[^"\']*)?)["\']', re.IGNORECASE)
JS_REF_REGEX = re.compile(
    r'(?:import|require|fetch|axios|System\.import)\s*\(?["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
    re.IGNORECASE,
)
JS_REDIRECT_REGEX = re.compile(
    r"""
    (?:window|document)?\.?location(?:\.href)?
    \s*
    (?:=|\.(?:assign|replace)\s*\()
    \s*
    (?:
        "(?P<dq>[^"]+)"
      | '(?P<sq>[^']+)'
      | (?P<bare>[^)\s;]+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class ReconRunnerOptions:
    urls: list[str]
    session_id: str
    same_origin_only: bool = True
    max_assets: int = 300
    max_depth: int = 2
    discovery_engine: str = "headless"
    katana_binary: str = "katana"
    include_sourcemaps: bool = True
    perform_analysis: bool = True
    wait_after_load_ms: int = 2500
    timeout_seconds: int = 20
    max_response_bytes: int = 12 * 1024 * 1024
    ingest_batch_size: int = 5


class ReconJobRunner:
    """
    Headless-assisted recon runner.
    - Uses Playwright response interception when available.
    - Falls back to parser-based HTML/JS discovery.
    - Reuses existing ingestion path for persistence and analysis.
    """

    def __init__(
        self,
        options: ReconRunnerOptions,
        db: Session,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ):
        self.options = options
        self.db = db
        self.progress_callback = progress_callback
        self.should_stop = should_stop or (lambda: False)
        self.assets: dict[str, dict[str, Any]] = {}
        self.fetch_cache: dict[str, dict[str, Any]] = {}
        self.sourcemap_processor = NativeSourceMapProcessor()
        self._seen_pages: set[str] = set()
        self._seen_js: set[str] = set()
        self._ingest_result: dict[str, Any] = {"stored": 0, "files": [], "fileIds": []}

    async def run(self) -> dict[str, Any]:
        started = self._now_iso()
        self._ingest_result = {"stored": 0, "files": [], "fileIds": []}
        
        for target in self.options.urls:
            if self.should_stop():
                break
            try:
                await self._discover_target(target)
            except Exception as exc:
                error_msg = str(exc)
                # Check for common URL/network errors
                if "Invalid IPv6 URL" in error_msg:
                    logger.error("IPv6 URL parsing error for target '%s': %s", target, exc)
                    raise ValueError("Invalid IPv6 URL") from exc
                elif "Name or service not known" in error_msg:
                    logger.error("DNS resolution failed for target '%s': %s", target, exc)
                    raise ValueError(f"DNS resolution failed: {target}") from exc
                elif "timeout" in error_msg.lower():
                    logger.error("Timeout during discovery for target '%s': %s", target, exc)
                    raise ValueError(f"Discovery timeout: {target}") from exc
                else:
                    logger.error("Unexpected error during discovery for target '%s': %s", target, exc)
                    raise

        payload_files: list[FileIn] = []
        if not self.should_stop():
            payload_files = await self._prepare_payload_files(incremental_ingest=True)
        ingest_result: dict[str, Any] = dict(self._ingest_result)

        coverage = self._build_coverage()
        return {
            "sessionId": self.options.session_id,
            "startedAt": started,
            "finishedAt": self._now_iso(),
            "cancelled": bool(self.should_stop()),
            "assets": sorted(self.assets.values(), key=lambda row: row.get("discoveredAt") or ""),
            "coverage": coverage,
            "ingestion": {
                "stored": int(ingest_result.get("stored") or 0),
                "fileIds": ingest_result.get("fileIds", []),
            },
        }

    async def _discover_target(self, target_url: str) -> None:
        engine = str(self.options.discovery_engine or "headless").strip().lower()
        if engine not in {"headless", "katana", "hybrid"}:
            engine = "headless"

        if engine in {"headless", "hybrid"}:
            headless_urls = await self._discover_with_headless(target_url)
            for url in headless_urls:
                self._register_candidate(url, target_url, "headless_response", 0)

        if engine in {"katana", "hybrid"}:
            katana_urls = await self._discover_with_katana(target_url)
            for url in katana_urls:
                self._register_candidate(url, target_url, "katana", 0)

        page_queue: list[tuple[str, int]] = [(target_url, 0)]
        while page_queue and not self.should_stop():
            page_url, depth = page_queue.pop(0)
            canonical_page = self._canonical_url(page_url)
            if canonical_page in self._seen_pages:
                continue
            self._seen_pages.add(canonical_page)

            page_fetch = await self._fetch_text(page_url)
            if not page_fetch.get("success"):
                self._register_failure(
                    page_url,
                    target_url,
                    "page_fetch",
                    0,
                    page_fetch.get("failureReason"),
                    page_fetch.get("error"),
                    page_fetch.get("statusCode"),
                )
                continue

            html = page_fetch.get("content") or ""
            for script_url in self._extract_script_urls_from_html(html, page_url):
                self._register_candidate(script_url, target_url, "html_script_src", 0)

            for inline_script in INLINE_SCRIPT_REGEX.findall(html):
                for inline_ref in self._extract_js_refs_from_js(inline_script, page_url):
                    self._register_candidate(inline_ref, target_url, "inline_script_ref", 0)

            if depth < 1:
                for redirect_url in self._extract_redirects(html, page_url):
                    if self._allow_url(redirect_url, target_url):
                        page_queue.append((redirect_url, depth + 1))

        js_queue: list[tuple[str, int, str]] = [
            (url, int(asset.get("depth") or 0), asset.get("discoveryMethod", "unknown"))
            for url, asset in self.assets.items()
        ]
        while js_queue and len(self.assets) < self.options.max_assets and not self.should_stop():
            js_url, depth, _method = js_queue.pop(0)
            canonical_js = self._canonical_url(js_url)
            if canonical_js in self._seen_js:
                continue
            self._seen_js.add(canonical_js)

            if depth >= self.options.max_depth:
                continue

            fetch_result = await self._fetch_text(js_url)
            if not fetch_result.get("success"):
                self._update_asset(
                    js_url,
                    failureReason=fetch_result.get("failureReason"),
                    error=fetch_result.get("error"),
                    httpStatus=fetch_result.get("statusCode"),
                )
                continue

            content = fetch_result.get("content") or ""
            if not self._is_likely_js(js_url, content, fetch_result.get("contentType")):
                self._update_asset(
                    js_url,
                    failureReason="non_js_content",
                    error="Fetched content was not JavaScript",
                    httpStatus=fetch_result.get("statusCode"),
                    contentType=fetch_result.get("contentType"),
                )
                continue

            for ref in self._extract_js_refs_from_js(content, js_url):
                if self.should_stop():
                    break
                if len(self.assets) >= self.options.max_assets:
                    break
                if not self._allow_url(ref, js_url):
                    self._register_failure(
                        ref,
                        target_url,
                        "js_ref",
                        depth + 1,
                        "blocked_by_scope",
                        "URL rejected by same-origin policy",
                    )
                    continue
                self._register_candidate(ref, target_url, "js_ref", depth + 1)
                js_queue.append((ref, depth + 1, "js_ref"))

            if self.options.include_sourcemaps:
                sourcemap_url = self._detect_sourcemap_url(
                    js_url=js_url,
                    content=content,
                    headers=fetch_result.get("headers") or {},
                )
                if sourcemap_url and not sourcemap_url.startswith("data:"):
                    map_fetch = await self._fetch_text(sourcemap_url)
                    if map_fetch.get("success"):
                        map_refs = self._extract_js_refs_from_sourcemap_sources(
                            sourcemap_url,
                            map_fetch.get("content") or "",
                        )
                        for ref in map_refs:
                            if self.should_stop():
                                break
                            if len(self.assets) >= self.options.max_assets:
                                break
                            if not self._allow_url(ref, js_url):
                                continue
                            self._register_candidate(ref, target_url, "sourcemap_sources", depth + 1)
                            js_queue.append((ref, depth + 1, "sourcemap_sources"))

    async def _prepare_payload_files(self, incremental_ingest: bool = False) -> list[FileIn]:
        payload_files: list[FileIn] = []
        ingest_batch: list[FileIn] = []
        batch_size = max(1, int(self.options.ingest_batch_size or 1))
        for asset in sorted(self.assets.values(), key=lambda row: row.get("discoveredAt") or ""):
            if self.should_stop():
                break
            url = asset.get("url")
            if not url:
                continue

            fetch_result = await self._fetch_text(url)
            if not fetch_result.get("success"):
                self._update_asset(
                    url,
                    failureReason=fetch_result.get("failureReason"),
                    error=fetch_result.get("error"),
                    httpStatus=fetch_result.get("statusCode"),
                )
                continue

            content = fetch_result.get("content") or ""
            content_type = fetch_result.get("contentType") or ""
            if not self._is_likely_js(url, content, content_type):
                self._update_asset(
                    url,
                    failureReason="non_js_content",
                    error="Fetched content was not JavaScript",
                    httpStatus=fetch_result.get("statusCode"),
                    contentType=content_type,
                    contentLength=len((content or "").encode("utf-8")),
                )
                continue

            source_map_url = None
            source_map_content: dict[str, Any] | None = None
            if self.options.include_sourcemaps:
                detected_map_url = self._detect_sourcemap_url(url, content, fetch_result.get("headers") or {})
                source_map_url = detected_map_url
                if detected_map_url:
                    if detected_map_url.startswith("data:"):
                        try:
                            source_map_content = self._parse_data_url_sourcemap(detected_map_url)
                            self._update_asset(url, sourceMapFetched=True, sourceMapDetectedUrl=detected_map_url)
                        except Exception as exc:
                            self._update_asset(
                                url,
                                sourceMapDetectedUrl=detected_map_url,
                                sourceMapError=str(exc),
                                sourceMapFetched=False,
                            )
                    else:
                        map_fetch = await self._fetch_text(detected_map_url)
                        if map_fetch.get("success"):
                            try:
                                source_map_content = json.loads(map_fetch.get("content") or "{}")
                                self._update_asset(url, sourceMapFetched=True, sourceMapDetectedUrl=detected_map_url)
                            except Exception as exc:
                                self._update_asset(
                                    url,
                                    sourceMapDetectedUrl=detected_map_url,
                                    sourceMapError=f"parse_failed: {exc}",
                                    sourceMapFetched=False,
                                )
                        else:
                            self._update_asset(
                                url,
                                sourceMapDetectedUrl=detected_map_url,
                                sourceMapError=map_fetch.get("failureReason"),
                                sourceMapFetched=False,
                            )

            content_hash = hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()
            payload_files.append(
                FileIn(
                    url=url,
                    contentHash=content_hash,
                    sessionId=self.options.session_id,
                    capturedAt=self._now_iso(),
                    contentType=content_type or "application/javascript",
                    contentEncoding=(fetch_result.get("headers") or {}).get("content-encoding"),
                    contentLength=len(content.encode("utf-8", errors="ignore")),
                    content=content,
                    sourceMapUrl=source_map_url,
                    sourceMapContent=source_map_content,
                    dependencies=[],
                )
            )
            if incremental_ingest:
                ingest_batch.append(payload_files[-1])
                if len(ingest_batch) >= batch_size:
                    self._flush_ingest_batch(ingest_batch)
                    ingest_batch.clear()
            self._update_asset(
                url,
                fetched=True,
                contentType=content_type,
                contentLength=len(content.encode("utf-8", errors="ignore")),
                httpStatus=fetch_result.get("statusCode"),
                failureReason=None,
                error=None,
            )
        if incremental_ingest and ingest_batch:
            self._flush_ingest_batch(ingest_batch)
        return payload_files

    def _flush_ingest_batch(self, batch: list[FileIn]) -> None:
        if not batch:
            return

        source_engine = str(self.options.discovery_engine or "headless").strip().lower()
        if source_engine not in {"headless", "katana", "hybrid"}:
            source_engine = "headless"
        perform_analysis = bool(self.options.perform_analysis)
        metadata = {
            "sessionId": self.options.session_id,
            "performAnalysis": perform_analysis,
            "disableAnalysis": not perform_analysis,
            "source": f"recon_{source_engine}",
        }
        ingest_payload = IngestionPayload(metadata=metadata, files=batch)
        try:
            partial = save_files(payload=ingest_payload, db=self.db)
        except Exception as exc:
            # Keep capture reliable even when analyzer output overflows DB jsonb limits.
            if bool(self.options.perform_analysis) and self._should_retry_without_analysis(exc):
                logger.warning(
                    "Recon batch analysis overflow for session %s; retrying ingest without analysis: %s",
                    self.options.session_id,
                    exc,
                )
                self._safe_rollback()
                retry_payload = IngestionPayload(
                    metadata={
                        "sessionId": self.options.session_id,
                        "performAnalysis": False,
                        "disableAnalysis": True,
                        "source": f"recon_{source_engine}",
                    },
                    files=batch,
                )
                partial = save_files(payload=retry_payload, db=self.db)
            else:
                self._safe_rollback()
                raise

        self._accumulate_ingestion_result(partial)
        self._apply_ingestion_results(partial)

    def _should_retry_without_analysis(self, exc: Exception) -> bool:
        message = str(exc or "").lower()
        markers = (
            "programlimitexceeded",
            "jsonb array elements exceeds the maximum",
            "total size of jsonb array elements exceeds the maximum",
        )
        return any(marker in message for marker in markers)

    def _safe_rollback(self) -> None:
        db = self.db
        if db is None:
            return
        rollback = getattr(db, "rollback", None)
        if not callable(rollback):
            return
        try:
            rollback()
        except Exception:
            logger.exception("Recon ingestion rollback failed for session %s", self.options.session_id)

    def _accumulate_ingestion_result(self, partial: dict[str, Any] | None) -> None:
        if not partial:
            return
        self._ingest_result["stored"] = int(self._ingest_result.get("stored") or 0) + int(partial.get("stored") or 0)

        existing_ids = set(self._ingest_result.get("fileIds") or [])
        for file_id in partial.get("fileIds") or []:
            if not file_id or file_id in existing_ids:
                continue
            existing_ids.add(file_id)
            self._ingest_result.setdefault("fileIds", []).append(file_id)

        self._ingest_result.setdefault("files", []).extend(partial.get("files") or [])

    def _apply_ingestion_results(self, ingest_result: dict[str, Any]) -> None:
        files = ingest_result.get("files") or []
        for row in files:
            url = row.get("url")
            if not url:
                continue
            analysis_status = ((row.get("analysis") or {}).get("status") or "").lower()
            analyzed = analysis_status in {"completed", "existing"}
            self._update_asset(
                url,
                ingested=True,
                analyzed=analyzed,
                analysisStatus=analysis_status or "skipped",
                fileId=row.get("fileId"),
            )

    async def _discover_with_headless(self, target_url: str) -> set[str]:
        """
        Optional Playwright interception. If Playwright isn't available, return an empty set.
        """
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return set()

        discovered: set[str] = set()
        timeout_ms = max(1000, int(self.options.timeout_seconds * 1000))
        wait_ms = max(0, int(self.options.wait_after_load_ms))

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                page = await context.new_page()

                def handle_response(response):
                    try:
                        req = response.request
                        resource_type = req.resource_type
                        resp_url = str(response.url)
                        if resource_type == "script" or resp_url.lower().endswith(".js"):
                            discovered.add(resp_url)
                    except Exception:
                        return

                page.on("response", handle_response)
                await page.goto(target_url, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(wait_ms)
                await context.close()
                await browser.close()
        except Exception as exc:
            logger.warning("Headless discovery failed for %s: %s", target_url, exc)
            return set()

        return discovered

    async def _discover_with_katana(self, target_url: str) -> set[str]:
        """
        Discover JS URLs using katana CLI jsonl output.
        Returns an empty set when katana is unavailable or fails.
        """
        binary = (self.options.katana_binary or "katana").strip() or "katana"
        if not shutil.which(binary):
            logger.warning("Katana binary '%s' not found. Skipping katana discovery.", binary)
            return set()

        depth = max(0, int(self.options.max_depth))
        timeout_seconds = max(3, int(self.options.timeout_seconds))
        # Reduce crawl window to be more aggressive about timeouts
        crawl_window_seconds = min(30, max(10, timeout_seconds * max(2, depth + 1)))
        command = [
            binary,
            "-u",
            target_url,
            "-d",
            str(depth),
            "-silent",
            "-j",
            "-timeout",
            str(timeout_seconds),
            "-ct",
            f"{crawl_window_seconds}s",
            "-jc",
            "-em", "js",  # Only extract JS extensions to reduce noise
            "-rate-limit", "5",  # Add rate limiting to avoid overwhelming servers
        ]

        discovered: set[str] = set()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=max(15, crawl_window_seconds + timeout_seconds),
            )
        except asyncio.TimeoutError:
            logger.warning("Katana discovery timed out for %s", target_url)
            return set()
        except Exception as exc:
            logger.warning("Katana discovery failed for %s: %s", target_url, exc)
            return set()

        stdout_text = (stdout or b"").decode("utf-8", errors="ignore")
        stderr_text = (stderr or b"").decode("utf-8", errors="ignore").strip()
        if process.returncode not in (0,):
            logger.warning(
                "Katana exited non-zero for %s (code=%s): %s",
                target_url,
                process.returncode,
                stderr_text[:400],
            )

        for raw_line in stdout_text.splitlines():
            line = (raw_line or "").strip()
            if not line:
                continue

            candidate_urls: list[str] = []
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        candidate_urls = self._extract_katana_candidate_urls(payload)
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.debug("Failed to parse katana JSON line: %s", exc)
                    # Try treating it as a plain URL
                    candidate_urls = [line.strip('{}')]
                except Exception as exc:
                    logger.warning("Unexpected error parsing katana line '%s': %s", line[:100], exc)
                    continue
            else:
                candidate_urls = [line]

            for candidate in candidate_urls:
                # Skip empty or obviously invalid candidates
                if not candidate or candidate in {"null", "undefined", ""}:
                    continue
                    
                try:
                    normalized = self._canonical_url(candidate)
                    if not normalized:
                        continue
                        
                    parsed = urlparse(normalized)
                    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                        continue
                        
                    if not self._looks_like_js_url(normalized):
                        continue
                        
                    discovered.add(normalized)
                except Exception as exc:
                    logger.debug("Failed to process candidate URL '%s': %s", candidate[:100], exc)

        return discovered

    def _extract_katana_candidate_urls(self, payload: dict[str, Any]) -> list[str]:
        urls: list[str] = []

        def maybe_add(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if text.startswith(("http://", "https://")):
                urls.append(text)

        maybe_add(payload.get("url"))
        maybe_add(payload.get("endpoint"))

        request = payload.get("request")
        if isinstance(request, dict):
            maybe_add(request.get("endpoint"))
            maybe_add(request.get("url"))

        response = payload.get("response")
        if isinstance(response, dict):
            maybe_add(response.get("endpoint"))
            maybe_add(response.get("url"))

        return list(dict.fromkeys(urls))

    async def _fetch_text(self, url: str) -> dict[str, Any]:
        cached = self.fetch_cache.get(url)
        if cached:
            return cached

        headers = {"User-Agent": "JS-Security-Extractor/3.0-Recon"}
        
        # Create fetcher with recon job options
        fetcher = robust_fetcher.__class__(
            timeout_seconds=self.options.timeout_seconds,
            connect_timeout_seconds=min(self.options.timeout_seconds // 3, 10),
            max_response_size=self.options.max_response_bytes,
        )
        
        fetch_result = await fetcher.fetch_text(url, headers=headers, check_content_type=False)
        
        # Convert to recon job format
        if fetch_result.success:
            result = {
                "success": True,
                "statusCode": fetch_result.status_code,
                "contentType": fetch_result.content_type,
                "headers": fetch_result.headers,
                "content": fetch_result.content,
                "finalUrl": fetch_result.final_url or url,
            }
        else:
            # Map error types to existing format
            failure_reason = "fetch_network"
            if fetch_result.error_type == "fetch_timeout":
                failure_reason = "fetch_timeout"
            elif fetch_result.error_type == "fetch_4xx":
                failure_reason = "fetch_4xx"
            elif fetch_result.error_type == "fetch_5xx":
                failure_reason = "fetch_5xx"
            elif fetch_result.error_type == "response_too_large":
                failure_reason = "parse_failed"
            
            result = {
                "success": False,
                "failureReason": failure_reason,
                "error": fetch_result.error_message or "Unknown error",
                "statusCode": fetch_result.status_code,
            }
        
        self.fetch_cache[url] = result
        return result

    def _extract_script_urls_from_html(self, html: str, base_url: str) -> list[str]:
        urls: list[str] = []
        for src in SCRIPT_SRC_REGEX.findall(html or ""):
            resolved = self._resolve_url(src, base_url)
            if resolved:
                urls.append(resolved)

        for src in ELEMENT_SCRIPT_REGEX.findall(html or ""):
            resolved = self._resolve_url(src, base_url)
            if resolved:
                urls.append(resolved)
        return urls

    def _extract_js_refs_from_js(self, content: str, base_url: str) -> list[str]:
        refs: list[str] = []
        for src in JS_REF_REGEX.findall(content or ""):
            resolved = self._resolve_url(src, base_url)
            if resolved:
                refs.append(resolved)
        for src in ELEMENT_SCRIPT_REGEX.findall(content or ""):
            resolved = self._resolve_url(src, base_url)
            if resolved:
                refs.append(resolved)
        return refs

    def _extract_redirects(self, content: str, base_url: str) -> list[str]:
        urls: list[str] = []
        for match in JS_REDIRECT_REGEX.findall(content or ""):
            url = next((part for part in match if part), None)
            resolved = self._resolve_url(url, base_url) if url else None
            if resolved:
                urls.append(resolved)
        return urls

    def _extract_js_refs_from_sourcemap_sources(self, sourcemap_url: str, map_content: str) -> list[str]:
        try:
            parsed = json.loads(map_content)
            sources = parsed.get("sources") or []
        except Exception:
            return []
        refs: list[str] = []
        for source in sources:
            if not isinstance(source, str):
                continue
            if ".js" not in source:
                continue
            cleaned = source.replace("webpack:///", "").replace("webpack://", "")
            resolved = self._resolve_url(cleaned, sourcemap_url)
            if resolved:
                refs.append(resolved)
        return refs

    def _detect_sourcemap_url(self, js_url: str, content: str, headers: dict[str, str]) -> str | None:
        if headers:
            header_candidate = headers.get("sourcemap") or headers.get("x-sourcemap")
            if header_candidate:
                return self._resolve_url(header_candidate, js_url)
        return self.sourcemap_processor._extract_sourcemap_url_from_content(content, js_url)

    def _parse_data_url_sourcemap(self, data_url: str) -> dict[str, Any]:
        # Preserve original payload in ingestion path by decoding directly for deterministic roundtrip.
        if "base64," in data_url:
            import base64

            payload = data_url.split("base64,", 1)[1]
            decoded = base64.b64decode(payload).decode("utf-8")
            return json.loads(decoded)
        payload = data_url.split(",", 1)[1]
        from urllib.parse import unquote

        return json.loads(unquote(payload))

    def _register_candidate(self, url: str, target_url: str, method: str, depth: int) -> None:
        if not url:
            return
        canonical = self._canonical_url(url)
        if canonical in self.assets:
            existing = self.assets[canonical]
            existing["dedupSkipped"] = True
            existing["duplicateCount"] = int(existing.get("duplicateCount") or 0) + 1
            self._emit(existing)
            return
        if len(self.assets) >= self.options.max_assets:
            return
        if not self._allow_url(canonical, target_url):
            self._register_failure(
                canonical,
                target_url,
                method,
                depth,
                "blocked_by_scope",
                "URL rejected by same-origin policy",
            )
            return

        asset = {
            "url": canonical,
            "targetUrl": target_url,
            "discoveryMethod": method,
            "depth": depth,
            "discovered": True,
            "fetched": False,
            "ingested": False,
            "analyzed": False,
            "dedupSkipped": False,
            "duplicateCount": 0,
            "discoveredAt": self._now_iso(),
            "failureReason": None,
            "error": None,
            "httpStatus": None,
            "contentType": None,
            "contentLength": None,
            "analysisStatus": "pending",
            "sourceMapDetectedUrl": None,
            "sourceMapFetched": False,
            "sourceMapError": None,
            "fileId": None,
        }
        self.assets[canonical] = asset
        self._emit(asset)

    def _register_failure(
        self,
        url: str,
        target_url: str,
        method: str,
        depth: int,
        reason: str | None,
        error: str | None,
        status_code: int | None = None,
    ) -> None:
        canonical = self._canonical_url(url)
        current = self.assets.get(canonical) or {
            "url": canonical,
            "targetUrl": target_url,
            "discoveryMethod": method,
            "depth": depth,
            "discovered": True,
            "fetched": False,
            "ingested": False,
            "analyzed": False,
            "dedupSkipped": False,
            "duplicateCount": 0,
            "discoveredAt": self._now_iso(),
            "analysisStatus": "pending",
            "sourceMapDetectedUrl": None,
            "sourceMapFetched": False,
            "sourceMapError": None,
            "fileId": None,
            "contentType": None,
            "contentLength": None,
        }
        current["failureReason"] = reason
        current["error"] = error
        current["httpStatus"] = status_code
        self.assets[canonical] = current
        self._emit(current)

    def _update_asset(self, url: str, **changes: Any) -> None:
        canonical = self._canonical_url(url)
        asset = self.assets.get(canonical)
        if not asset:
            return
        asset.update(changes)
        self._emit(asset)

    def _emit(self, asset: dict[str, Any]) -> None:
        if self.progress_callback:
            self.progress_callback(dict(asset))

    def _allow_url(self, candidate_url: str, base_url: str) -> bool:
        if not candidate_url:
            return False
        if not candidate_url.startswith(("http://", "https://")):
            return False
        if not self.options.same_origin_only:
            return True
        return urlparse(candidate_url).netloc == urlparse(base_url).netloc

    def _resolve_url(self, maybe_url: str | None, base_url: str) -> str | None:
        if not maybe_url:
            return None
        try:
            return self._canonical_url(urljoin(base_url, maybe_url))
        except Exception:
            return None

    def _canonical_url(self, url: str) -> str:
        """
        Canonicalize URL with better error handling for malformed URLs.
        """
        try:
            # Handle potential issues with IPv6 and malformed URLs
            url = url.strip()
            if not url:
                return ""
            
            # Basic validation before parsing
            if not url.startswith(("http://", "https://", "//")):
                if not url.startswith(("ftp://", "file://", "data:")):
                    # Assume https for bare domain names
                    url = f"https://{url}"
            
            parsed = urlparse(url)
            scheme = parsed.scheme.lower() or "https"
            netloc = parsed.netloc
            
            # Handle IPv6 addresses properly
            if netloc and ":" in netloc and not netloc.startswith("["):
                # Check if this might be an IPv6 address without brackets
                try:
                    import ipaddress
                    # Extract potential IPv6 part (before port if any)
                    host_part = netloc.split("]")[0].lstrip("[")
                    if ":" in host_part and not host_part.count(":") == 1:  # Not just host:port
                        try:
                            ipaddress.IPv6Address(host_part)
                            # It's a valid IPv6, ensure it's bracketed
                            port_part = ""
                            if "]:" in netloc:
                                port_part = netloc.split("]:")[-1]
                            elif netloc.count(":") > host_part.count(":"):
                                port_part = netloc.split(":")[-1]
                            netloc = f"[{host_part}]" + (f":{port_part}" if port_part else "")
                        except (ipaddress.AddressValueError, ValueError):
                            pass
                except ImportError:
                    pass
            
            netloc = netloc.lower()
            path = parsed.path or "/"
            query = f"?{parsed.query}" if parsed.query else ""
            
            return f"{scheme}://{netloc}{path}{query}"
            
        except Exception as exc:
            logger.warning("Failed to canonicalize URL '%s': %s", url, exc)
            # Return the original URL as fallback, but ensure it's at least a string
            return str(url) if url else ""

    def _looks_like_js_url(self, url: str) -> bool:
        lowered = (url or "").lower()
        parsed = urlparse(lowered)
        path = parsed.path or ""
        if path.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")):
            return True
        if ".js" in path:
            return True
        return ".js" in (parsed.query or "")

    def _is_likely_js(self, url: str, content: str, content_type: str | None) -> bool:
        lowered_url = (url or "").lower()
        if lowered_url.endswith((".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx")):
            return True
        lowered_type = (content_type or "").lower()
        if "javascript" in lowered_type or "ecmascript" in lowered_type:
            return True
        probe = (content or "")[:250].lower()
        return any(token in probe for token in ("function", "const ", "let ", "var ", "import ", "export "))

    def _build_coverage(self) -> dict[str, Any]:
        assets = list(self.assets.values())
        failure_reasons = {reason: 0 for reason in MISS_REASON_TAXONOMY}
        discovered_js = len(assets)
        fetched_js = 0
        ingested_js = 0
        analyzed_js = 0
        map_detected = 0
        map_fetched = 0
        dedup_skipped = 0

        for asset in assets:
            if asset.get("fetched"):
                fetched_js += 1
            if asset.get("ingested"):
                ingested_js += 1
            if asset.get("analyzed"):
                analyzed_js += 1
            if asset.get("sourceMapDetectedUrl"):
                map_detected += 1
            if asset.get("sourceMapFetched"):
                map_fetched += 1
            dedup_skipped += max(0, int(asset.get("duplicateCount") or 0))
            reason = asset.get("failureReason")
            if reason:
                normalized_reason = self._normalize_failure_reason(str(reason))
                failure_reasons[normalized_reason] = failure_reasons.get(normalized_reason, 0) + 1
            elif not asset.get("fetched"):
                failure_reasons["not_seen"] = failure_reasons.get("not_seen", 0) + 1

        failure_reasons["dedup_skipped"] = dedup_skipped
        map_failed = max(0, map_detected - map_fetched)

        return {
            "discovered_js": discovered_js,
            "fetched_js": fetched_js,
            "ingested_js": ingested_js,
            "analyzed_js": analyzed_js,
            "map_detected": map_detected,
            "map_fetched": map_fetched,
            "map_processed": map_fetched,
            "map_failed": map_failed,
            "failure_reasons": failure_reasons,
            "rates": {
                "fetchPct": self._pct(fetched_js, discovered_js),
                "ingestPct": self._pct(ingested_js, discovered_js),
                "analysisPct": self._pct(analyzed_js, discovered_js),
                "mapFetchPct": self._pct(map_fetched, map_detected),
            },
        }

    def _now_iso(self) -> str:
        return datetime.utcnow().isoformat()

    def _normalize_failure_reason(self, reason: str) -> str:
        normalized = (reason or "").strip().lower()
        if normalized in MISS_REASON_TAXONOMY:
            return normalized
        if normalized.startswith("fetch_4"):
            return "fetch_4xx"
        if normalized.startswith("fetch_5"):
            return "fetch_5xx"
        if normalized in {"fetch_network", "fetch_error", "network_error"}:
            return "parse_failed"
        return "parse_failed"

    def _pct(self, numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
