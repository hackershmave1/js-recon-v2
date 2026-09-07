import { useState } from "react";

type ArtifactKind = "curl" | "http" | "websocat" | "python";

// Generate a minimal Python/httpx snippet from a raw HTTP request string.
// Labeled as derived (server-side http artifact → client-side conversion).
function httpToPython(http: string): string {
  const lines = http.split("\n");
  const requestLine = lines[0] ?? "";
  const methodMatch = requestLine.match(/^(\w+)\s+(\S+)/);
  const method = methodMatch?.[1]?.toLowerCase() ?? "get";
  const path = methodMatch?.[2] ?? "/";

  const headers: Record<string, string> = {};
  let host = "";
  for (const line of lines.slice(1)) {
    const m = line.match(/^([\w-]+):\s*(.+)/);
    if (!m) continue;
    const key = m[1].toLowerCase();
    if (key === "host") { host = m[2].trim(); continue; }
    headers[m[1]] = m[2].trim();
  }

  const url = host ? `https://${host}${path}` : path;
  const headerStr = Object.entries(headers).length > 0
    ? `headers=${JSON.stringify(headers)}, `
    : "";
  return `# derived from server-provided HTTP artifact\nimport httpx\nr = httpx.${method}("${url}", ${headerStr})\nprint(r.status_code, r.text)`;
}

export function ArtifactTabs({ artifacts }: {
  artifacts: { curl?: string; http?: string; websocat?: string } | null;
}) {
  const tabs: { key: ArtifactKind; label: string; content: string }[] = [];
  if (artifacts?.curl) tabs.push({ key: "curl", label: "curl", content: artifacts.curl });
  if (artifacts?.http) tabs.push({ key: "http", label: "HTTP", content: artifacts.http });
  if (artifacts?.websocat) tabs.push({ key: "websocat", label: "websocat", content: artifacts.websocat });
  if (artifacts?.http) tabs.push({ key: "python", label: "Python (derived)", content: httpToPython(artifacts.http) });

  const [activeKey, setActiveKey] = useState<ArtifactKind | null>(tabs[0]?.key ?? null);
  const [copied, setCopied] = useState(false);

  if (tabs.length === 0) return <p className="muted pb-not-probeable">Not probeable from this surface.</p>;

  const active = tabs.find((t) => t.key === activeKey) ?? tabs[0];

  async function copy() {
    await navigator.clipboard.writeText(active.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="pb-artifact">
      <div className="pb-tabs" role="tablist">
        {tabs.map((t) => (
          <button
            key={t.key}
            role="tab"
            type="button"
            aria-selected={active.key === t.key}
            className={"pb-tab" + (active.key === t.key ? " pb-tab-active" : "")}
            onClick={() => { setActiveKey(t.key); setCopied(false); }}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="pb-artifact-body" role="tabpanel">
        <pre className="pb-artifact-code">{active.content}</pre>
        <button type="button" className="pb-copy" onClick={copy}>
          {copied ? "Copied ✓" : "Copy"}
        </button>
      </div>
    </div>
  );
}
