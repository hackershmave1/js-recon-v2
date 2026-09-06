export class SourceMapDetector {
  detect(content, fileUrl) {
    const urlComment = this.extractFromComment(content);
    if (urlComment) {
      return this.resolveSourceMapUrl(urlComment, fileUrl);
    }
    return null;
  }

  // The `SourceMap:` / `X-SourceMap` response header (DEBT D45c). Chrome already handed us
  // these on the script's onCompleted response (background.extractMetadata lowercases every
  // header name), but detection was inline-comment-only, so a bundle that ships its map ref
  // ONLY in the header (no `//# sourceMappingURL=` trailer) was silently missed. Consulted
  // as a fallback AFTER the comment so the comment stays authoritative. Returns a resolved
  // absolute URL (or a `data:` URI) or null.
  detectFromHeaders(headers, fileUrl) {
    if (!headers || typeof headers !== 'object') return null;
    const raw = headers['sourcemap'] || headers['x-sourcemap'];
    if (!raw || typeof raw !== 'string' || !raw.trim()) return null;
    return this.resolveSourceMapUrl(raw.trim(), fileUrl);
  }

  // The conventional `<file>.js.map` sibling, tried as a LAST resort when neither a comment
  // nor a header points at a map (DEBT D45c). Strips query/fragment so `app.js?v=2` probes
  // `app.js.map`. The caller fetches this single-shot (no retry) and accepts it only if the
  // body parses as a real source map, so a SPA 200 HTML fallback can't masquerade as one.
  // Returns null for a directory-ish or unparseable URL.
  conventionalMapUrl(fileUrl) {
    try {
      const u = new URL(fileUrl);
      u.hash = '';
      u.search = '';
      if (!u.pathname || u.pathname.endsWith('/')) return null;
      return u.href + '.map';
    } catch (e) {
      return null;
    }
  }

  extractFromComment(content) {
    const patterns = [
      /\/\/# sourceMappingURL=(.+?)(?:\n|$)/,
      /\/\*# sourceMappingURL=(.+?)\*\//
    ];

    for (const pattern of patterns) {
      const match = content.match(pattern);
      if (match) {
        return match[1].trim();
      }
    }
    return null;
  }

  resolveSourceMapUrl(mapUrl, jsFileUrl) {
    if (mapUrl.startsWith('data:')) {
      return mapUrl;
    }

    let resolvedMapUrl;
    if (mapUrl.startsWith('http://') || mapUrl.startsWith('https://')) {
      resolvedMapUrl = mapUrl;
    } else {
      try {
        const baseUrl = jsFileUrl.substring(0, jsFileUrl.lastIndexOf('/') + 1);
        resolvedMapUrl = new URL(mapUrl, baseUrl).href;
      } catch (e) {
        return null;
      }
    }

    // Cross-origin source map guard: a hostile page could set
    // `//# sourceMappingURL=https://internal.corp/secret` to cause the extension
    // to read an arbitrary host. Reject any map URL whose origin differs from the
    // parent JS file — same-origin maps are always valid; cross-origin ones are not.
    try {
      const jsOrigin = new URL(jsFileUrl).origin;
      const mapOrigin = new URL(resolvedMapUrl).origin;
      if (jsOrigin !== mapOrigin) {
        return null;
      }
    } catch (e) {
      return null;
    }

    return resolvedMapUrl;
  }
}
