// syntax.js tests: language detection, per-line tokenization, in-file find ranges.
// Run: node tests/test_syntax.mjs
import assert from 'node:assert/strict';
import { detectLanguage, highlightLines, lineText, matchRanges } from '../src/syntax.js';

// --- detectLanguage ---
assert.equal(detectLanguage('src/app.js'), 'javascript');
assert.equal(detectLanguage('a/b/Foo.tsx'), 'tsx');
assert.equal(detectLanguage('style.css'), 'css');
assert.equal(detectLanguage('data.json'), 'json');
assert.equal(detectLanguage('webpack://x/no-ext'), 'javascript'); // default
assert.equal(detectLanguage('chunk.js?v=123'), 'javascript');     // query stripped

// --- highlightLines: per-line segments, deepest type wins ---
const src = 'const x = 1;\n// note\nfunction go() { return "hi"; }';
const { lang, lines } = highlightLines(src, 'a.js');
assert.equal(lang, 'javascript');
assert.equal(lines.length, 3, 'three source lines');
// line 1 has a keyword and a number among its segments
const types1 = lines[0].map((s) => s.type);
assert.ok(types1.includes('keyword'), 'const is a keyword');
assert.ok(types1.includes('number'), '1 is a number');
// line 2 is a comment
assert.ok(lines[1].some((s) => s.type === 'comment'), 'line 2 comment');
// raw text round-trips per line
assert.equal(lineText(lines[0]), 'const x = 1;');
assert.equal(lineText(lines[1]), '// note');

// --- segment spanning a newline (block comment) splits across lines ---
const blk = highlightLines('/* a\nb */\nx', 'a.js');
assert.equal(blk.lines.length, 3, 'block comment spans two lines + one code line');
assert.equal(lineText(blk.lines[0]), '/* a');
assert.equal(lineText(blk.lines[1]), 'b */');

// --- unknown-but-defaulted vs plain: huge content falls back to plain (lang null) ---
const big = highlightLines('x'.repeat(400001), 'a.js');
assert.equal(big.lang, null, 'oversized content is not tokenized');
assert.equal(big.lines.length, 1);

// --- matchRanges: case-insensitive, non-overlapping ---
assert.deepEqual(matchRanges('FooBarfoo', 'foo'), [[0, 3], [6, 9]]);
assert.deepEqual(matchRanges('abc', 'x'), []);
assert.deepEqual(matchRanges('abc', ''), []);

console.log('syntax tests: all assertions passed');
