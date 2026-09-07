import type { ReconstructedRequest } from "../../api/types";

const DASH = "—";

export function ParamsTable({ req }: { req: ReconstructedRequest }) {
  const rows = [
    ...req.query_params.map((p) => ({ name: p.name, in: "query", example: p.example })),
    ...req.body_params.map((name) => ({ name, in: "body", example: null })),
  ];
  if (rows.length === 0) return null;
  return (
    <table className="pb-params" aria-label="Request parameters">
      <thead>
        <tr>
          <th>Name</th>
          <th>In</th>
          <th>Example</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.in}:${r.name}`}>
            <td><code>{r.name}</code></td>
            <td className="pb-param-in">{r.in}</td>
            <td className="pb-param-ex">{r.example ?? DASH}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
