// syntax.js — Prism-based tokenization for the code viewer. Pure (no Preact) so it
// unit-tests in node: turns file content into per-line coloured segments and computes
// in-file search match ranges. We use prism-core + explicit language components (not
// the full prism.js) to avoid Prism's DOM auto-highlight side effects.
import Prism from 'prismjs/components/prism-core.js';
import 'prismjs/components/prism-clike.js';
import 'prismjs/components/prism-markup.js';
import 'prismjs/components/prism-css.js';
import 'prismjs/components/prism-javascript.js';
import 'prismjs/components/prism-json.js';
import 'prismjs/components/prism-typescript.js';
import 'prismjs/components/prism-jsx.js';
import 'prismjs/components/prism-tsx.js';

// Tokenizing a multi-megabyte minified line is pointless and slow — fall back to plain
// rendering above this size (find still works; only colours are dropped).
const HIGHLIGHT_MAX_BYTES = 350000;

const EXT_LANG = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
  ts: 'typescript', tsx: 'tsx', json: 'json', css: 'css', scss: 'css',
  html: 'markup', htm: 'markup', xml: 'markup', svg: 'markup', vue: 'markup'
};

// Reconstructed sources are overwhelmingly JS, so unknown/extension-less paths
// (e.g. webpack:// virtual paths) default to javascript rather than plain text.
export function detectLanguage(path) {
  const m = /\.([a-z0-9]+)(?:[?#].*)?$/i.exec(path || '');
  const ext = m ? m[1].toLowerCase() : '';
  return EXT_LANG[ext] || 'javascript';
}

// Prism token type → colour. Unmapped types render in the default code colour.
export const TOKEN_COLOR = {
  keyword: '#C792EA', boolean: '#FF8A47', constant: '#FF8A47', number: '#FF8A47',
  string: '#CDEB45', char: '#CDEB45', 'template-string': '#CDEB45', 'attr-value': '#CDEB45',
  comment: '#5C6680', prolog: '#5C6680', doctype: '#5C6680', cdata: '#5C6680',
  function: '#6BA8FF', property: '#6BA8FF', url: '#6BA8FF',
  'class-name': '#FFC73D', 'attr-name': '#FFC73D',
  operator: '#5BD6C0', regex: '#5BD6C0', builtin: '#5BD6C0', entity: '#5BD6C0',
  punctuation: '#7E8AA3', tag: '#FF6B8A', selector: '#FF6B8A', important: '#FF4D5E',
  variable: '#ECEFF6', parameter: '#ECEFF6', interpolation: '#ECEFF6'
};

// Flatten Prism's nested token tree into a flat [{text, type}] list; the deepest
// token type wins, while bare strings inherit their parent token's type.
function flatten(tokens, out, parentType) {
  for (const tok of tokens) {
    if (typeof tok === 'string') {
      out.push({ text: tok, type: parentType || null });
    } else {
      const t = tok.alias ? (Array.isArray(tok.alias) ? tok.alias[0] : tok.alias) : tok.type;
      if (typeof tok.content === 'string') out.push({ text: tok.content, type: t });
      else if (Array.isArray(tok.content)) flatten(tok.content, out, t);
      else flatten([tok.content], out, t);
    }
  }
}

// Returns { lang, lines } where lines[i] is an array of {text, type} segments for
// source line i+1. Segments that span a newline (block comments, template literals)
// are split correctly across lines.
export function highlightLines(content, path) {
  const lang = detectLanguage(path);
  const grammar = Prism.languages[lang];
  const applied = !!grammar && (content || '').length <= HIGHLIGHT_MAX_BYTES;
  const segs = [];
  if (applied) {
    try { flatten(Prism.tokenize(content, grammar), segs, null); }
    catch (e) { segs.length = 0; segs.push({ text: content, type: null }); }
  } else {
    segs.push({ text: content, type: null });
  }

  const lines = [[]];
  for (const s of segs) {
    const parts = s.text.split('\n');
    for (let i = 0; i < parts.length; i += 1) {
      if (i > 0) lines.push([]);
      if (parts[i] !== '') lines[lines.length - 1].push({ text: parts[i], type: s.type });
    }
  }
  return { lang: applied ? lang : null, lines };
}

// Raw text of one line (segments joined) — used for find + match positioning.
export function lineText(segs) {
  let s = '';
  for (const seg of segs) s += seg.text;
  return s;
}

// Case-insensitive substring match ranges [start, end) within one line of text.
export function matchRanges(text, queryLower) {
  if (!queryLower) return [];
  const ranges = [];
  const hay = text.toLowerCase();
  let i = hay.indexOf(queryLower);
  while (i !== -1) {
    ranges.push([i, i + queryLower.length]);
    i = hay.indexOf(queryLower, i + queryLower.length);
  }
  return ranges;
}
