import { useMemo, useState } from "react";
import { costPerRun, formatCost, type CatalogModel, type ModelCatalog } from "../../api/llmCatalog";

const MAX_ROWS = 200; // the catalog has hundreds of models; search narrows it

export function ModelPicker({ catalog, title, onPick, onClose }: {
  catalog: ModelCatalog; title: string; onPick: (id: string) => void; onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"price" | "name">("price");
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const cost = (m: CatalogModel) => costPerRun(m, catalog.estimate);
    return catalog.models
      .filter((m) => !q || m.id.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))
      .sort((a, b) => (sort === "price" ? cost(a) - cost(b) : a.name.localeCompare(b.name)));
  }, [catalog, query, sort]);
  const basis = catalog.estimate.basis === "history"
    ? `based on your last ${catalog.estimate.runs} runs`
    : "assumed, no history yet";

  return (
    <div className="model-picker" role="dialog" aria-label={title}>
      <div className="model-picker-head">
        <strong>{title}</strong>
        <button type="button" className="shell-btn" onClick={onClose}>Close</button>
      </div>
      {!catalog.available ? (
        <p className="settings-error">The OpenRouter catalog is unavailable right now. Type a model ID instead.</p>
      ) : (
        <>
          <div className="model-picker-controls">
            <input aria-label="Search models" placeholder="Search by name or ID" value={query}
              onChange={(e) => setQuery(e.target.value)} />
            <select aria-label="Sort models" value={sort} onChange={(e) => setSort(e.target.value as "price" | "name")}>
              <option value="price">Cheapest first</option>
              <option value="name">Name</option>
            </select>
          </div>
          <p className="settings-hint">
            Cost per threat model, {basis}{catalog.stale ? " · catalog may be out of date" : ""}.
          </p>
          <ul className="model-picker-list">
            {rows.slice(0, MAX_ROWS).map((m) => (
              <li key={m.id}>
                <button type="button" onClick={() => onPick(m.id)}>
                  <span className="model-picker-name">{m.name}</span>
                  <code>{m.id}</code>
                  <span>{Math.round(m.context_length / 1000)}k ctx</span>
                  <span>{formatCost(costPerRun(m, catalog.estimate))}</span>
                </button>
              </li>
            ))}
          </ul>
          {rows.length > MAX_ROWS && <p className="settings-hint">Showing {MAX_ROWS} of {rows.length}; refine the search.</p>}
        </>
      )}
    </div>
  );
}
