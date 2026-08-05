const splitOp = (v) => { const [m, ...rest] = v.split(" "); const path = rest.join(" ").split("?")[0]; return [m, path]; };

export function scoreFindings(payload, key) {
  const findings = payload.findings || [];
  const cov = payload.coverage || {};
  const endpoints = findings.filter(f => f.type === "endpoint");
  const params = findings.filter(f => f.type === "param");
  const secrets = findings.filter(f => f.type === "secret");

  const endpointMatches = (e) => endpoints.some(f => {
    const [m, op] = splitOp(f.value);
    if (m !== e.method) return false;
    if (e.host) return (f.occurrences || []).some(o => o.host === e.host) && (!e.operation || op === e.operation);
    return op === e.operation;
  });
  const paramMatches = (p) => params.some(f => f.attributes && f.attributes.location === p.location && f.attributes.name === p.name);

  const results = key.should_find.map(e => ({ id: e.id, found: endpointMatches(e), params: (e.params || []).map(p => ({ ...p, found: paramMatches(p) })) }));
  const missedEndpoints = results.filter(r => !r.found).map(r => r.id);
  const missedParams = results.flatMap(r => r.params.filter(p => !p.found).map(p => `${r.id}:${p.location}:${p.name}`));

  const provCounts = {};
  for (const s of secrets) {
    const prov = (s.value || "").split(":")[0] || (s.attributes && s.attributes.rule ? String(s.attributes.rule).split(".")[1]?.toLowerCase() : "");
    if (prov) provCounts[prov] = (provCounts[prov] || 0) + 1;
  }
  const secretMisses = Object.entries(key.secrets.must)
    .filter(([p, n]) => (provCounts[p] || 0) < n)
    .map(([p, n]) => `${p}>=${n} (got ${provCounts[p] || 0})`);

  const covFail = [];
  if (!key.coverage_asserts.source_map_ok.includes(cov.source_map)) covFail.push(`source_map=${cov.source_map} not in ${key.coverage_asserts.source_map_ok.join("|")}`);
  if (key.coverage_asserts.require_sources_recovered && !(cov.sources_recovered > 0)) covFail.push(`sources_recovered=${cov.sources_recovered}`);
  if ((cov.unattributed || 0) < key.coverage_asserts.min_unattributed) covFail.push(`unattributed=${cov.unattributed} < ${key.coverage_asserts.min_unattributed}`);
  const recovered = [...endpoints, ...params].some(f => (f.occurrences || []).some(o => o.source_path && o.source_path !== "input.js"));
  if (!recovered) covFail.push("no recovered source_path (all input.js)");

  const blindViolations = (key.known_blind_spots || []).filter(b => b.probe && endpoints.some(f => splitOp(f.value)[1] === b.probe)).map(b => b.id);

  const known = new Set(key.should_find.filter(e => e.operation).map(e => `${e.method} ${e.operation}`));
  const hostSet = new Set(key.should_find.filter(e => e.host).map(e => e.host));
  const unexpected = endpoints.filter(f => {
    const [m, op] = splitOp(f.value);
    if (known.has(`${m} ${op}`)) return false;
    if ((f.occurrences || []).some(o => hostSet.has(o.host))) return false;
    return true;
  }).map(f => f.value);

  const pass = missedEndpoints.length === 0 && missedParams.length === 0 && secretMisses.length === 0 && covFail.length === 0;
  return { pass, missedEndpoints, missedParams, secretMisses, covFail, provCounts, blindViolations, unexpected };
}
