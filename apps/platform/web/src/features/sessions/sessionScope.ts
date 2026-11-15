// What a Sessions card says about a session's scope, and what it is called when it has no host yet.
// Two cards for one host used to look identical; the scope is what tells them apart.

const SHOWN = 2;

export function scopeSummary(scopeHosts: string[]): { text: string; title: string } {
  const hosts = scopeHosts.filter(Boolean);
  if (hosts.length === 0) return { text: "No scope set", title: "This session has no scope yet" };
  const rest = hosts.length - SHOWN;
  return {
    text: rest > 0 ? `${hosts.slice(0, SHOWN).join(", ")} +${rest}` : hosts.join(", "),
    title: hosts.join(", "),
  };
}

// A session whose only capture ended with no files keeps no host ("—"): name it, don't print a dash.
export function sessionTitle(s: { name: string | null; host: string }): string {
  const host = (s.host || "").trim();
  if (s.name) return s.name;
  return host && host !== "—" ? host : "Empty session";
}
