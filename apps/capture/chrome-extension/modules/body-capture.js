// body-capture.js — pure helpers for capturing API request/response BODIES (DEBT D45b2).
// Request bodies ride the webRequest `onBeforeRequest.requestBody` (no main world); response
// bodies come from the opt-in main-world hook. These helpers decode, REDACT obvious credentials,
// and size-cap the text before it ever leaves the worker. Dependency-free (importable in tests).

// Credential-bearing keys to redact in JSON or urlencoded-form bodies (a login POST carries the
// password; a token-refresh carries the refresh token). Best-effort — the platform also runs
// Kingfisher over the body server-side to catch what this misses. Matches `"key":"val"`,
// `"key":val`, and `key=val` (form), replacing only the VALUE.
const CRED_KEY = /("?\b(?:password|passwd|pwd|secret|client[_-]?secret|api[_-]?key|apikey|access[_-]?key|authorization|bearer|access[_-]?token|refresh[_-]?token|id[_-]?token|session[_-]?token|token|jwt|otp|pin)"?\s*[:=]\s*)("(?:[^"\\]|\\.)*"|'[^']*'|[^&\s,}\]]+)/gi;

// Decode a webRequest `details.requestBody` into { text, type }. Handles urlencoded `formData`
// and `raw` byte chunks (JSON/GraphQL). `maxChars` bounds how much is materialized so a multi-MB
// upload isn't fully assembled just to keep a small cap (review Finding 4). Returns null when
// there's no usable body.
export function decodeRequestBody(requestBody, maxChars = Infinity) {
  if (!requestBody || typeof requestBody !== 'object') return null;
  if (requestBody.formData && typeof requestBody.formData === 'object') {
    const parts = [];
    let len = 0;
    for (const [k, vals] of Object.entries(requestBody.formData)) {
      const arr = Array.isArray(vals) ? vals : [vals];
      for (const v of arr) {
        const pair = `${k}=${v}`;
        parts.push(pair);
        len += pair.length + 1;
        if (len >= maxChars) return { text: parts.join('&').slice(0, maxChars), type: 'form' };
      }
    }
    return parts.length ? { text: parts.join('&'), type: 'form' } : null;
  }
  if (Array.isArray(requestBody.raw) && requestBody.raw.length) {
    try {
      const decoder = new TextDecoder('utf-8', { fatal: false });
      let text = '';
      for (const chunk of requestBody.raw) {
        if (chunk && chunk.bytes) text += decoder.decode(chunk.bytes, { stream: true });
        if (text.length >= maxChars) break;
      }
      text += decoder.decode();
      if (!text) return null;
      return { text: text.length > maxChars ? text.slice(0, maxChars) : text, type: 'raw' };
    } catch (e) {
      return null;
    }
  }
  return null;
}

// Replace obvious credential VALUES with a placeholder (keeps the key so the request shape is
// still legible). Never throws.
export function redactBody(text) {
  if (typeof text !== 'string' || !text) return '';
  return text.replace(CRED_KEY, (_m, key) => `${key}"[REDACTED]"`);
}

// Truncate to a byte-ish char cap so one huge payload can't bloat the upload.
export function capBody(text, cap) {
  if (typeof text !== 'string') return '';
  return text.length <= cap ? text : text.slice(0, cap);
}

// Convenience: decode → redact → cap in one call. Returns { text, type } or null. The decode is
// bounded to ~2x cap (review Finding 4): the 2x margin lets a credential value straddling the cap
// still be redacted before the final trim, without materializing a huge upload in full.
export function prepareRequestBody(requestBody, cap) {
  const decoded = decodeRequestBody(requestBody, cap * 2);
  if (!decoded || !decoded.text) return null;
  return { text: capBody(redactBody(decoded.text), cap), type: decoded.type };
}
