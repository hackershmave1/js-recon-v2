// Lazy Prism (v1) JavaScript highlighter that yields PER-LINE spans, so the code
// viewer can keep its line-number / finding-mark / focus-line structure.
//
// We tokenize and split token CONTENT on "\n" ourselves (design fix S3) instead
// of Prism.highlight(...).split("\n"): a multi-line token (template literal, block
// comment) is ONE token whose text spans lines, so splitting rendered HTML would
// tear tags across lines and produce unbalanced markup.

export interface HighlightedSpan { text: string; className: string; }

// Minimal structural shape of a Prism token — avoids importing @types/prismjs'
// `export =` form. `content` can itself be a token (array), hence the recursion.
interface PrismToken { type: string; alias?: string | string[]; content: PrismNode | PrismNode[]; }
type PrismNode = string | PrismToken;

const isToken = (n: PrismNode): n is PrismToken => typeof n !== "string";

// Flatten Prism's (possibly nested) token tree into flat segments whose text may
// still contain "\n". Nested token classes are concatenated so CSS like
// `.token.string` still matches after we drop Prism's nested-span structure.
function collectSegments(node: PrismNode, classes: string[], out: HighlightedSpan[]): void {
  if (!isToken(node)) {
    out.push({ text: node, className: classes.length ? ["token", ...classes].join(" ") : "" });
    return;
  }
  const aliases = Array.isArray(node.alias) ? node.alias : node.alias ? [node.alias] : [];
  const next = [...classes, node.type, ...aliases];
  const content = node.content;
  const children = Array.isArray(content) ? content : [content];
  for (const child of children) collectSegments(child, next, out);
}

// Break flat (newline-carrying) segments into one span array per line. An empty
// line is an empty array — line count matches text.split("\n").
function splitIntoLines(segments: HighlightedSpan[]): HighlightedSpan[][] {
  const lines: HighlightedSpan[][] = [[]];
  for (const seg of segments) {
    const parts = seg.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) lines.push([]);
      if (part !== "") lines[lines.length - 1].push({ text: part, className: seg.className });
    });
  }
  return lines;
}

export async function highlightJsLines(code: string): Promise<HighlightedSpan[][]> {
  const { default: Prism } = await import("prismjs");
  // The core "prismjs" bundle already ships Prism.languages.javascript.
  const tokens = Prism.tokenize(code, Prism.languages.javascript) as unknown as PrismNode[];
  const segments: HighlightedSpan[] = [];
  for (const t of tokens) collectSegments(t, [], segments);
  return splitIntoLines(segments);
}
