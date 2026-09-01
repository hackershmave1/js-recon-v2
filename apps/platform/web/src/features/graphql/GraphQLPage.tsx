import type { FindingsResponse, Finding, Occurrence, SourceJump } from "../../api/types";
import "./graphql.css";

// The run's GraphQL surface: operations/fragments the analyze stage promoted to
// findings (type "graphql"), grouped by kind. v1 is "promote-only" — we render the
// documents the static extractor could resolve; see DETECTION_NOTE for the honesty
// caveat that keeps these counts read as a floor, not a ceiling.

const KIND_ORDER = ["query", "mutation", "subscription", "fragment"] as const;
type GqlKind = (typeof KIND_ORDER)[number];
const KIND_LABELS: Record<GqlKind, string> = {
  query: "Queries",
  mutation: "Mutations",
  subscription: "Subscriptions",
  fragment: "Fragments",
};

// Load-bearing honesty note (v1 promote-only scope). A CONSTANT — never interpolated
// from a live count, which would imply a completeness the static pass can't claim:
// minified/renamed gql tags aren't fully resolved, so the shown counts are a floor.
const DETECTION_NOTE =
  "Static detection currently resolves gql-tagged and inline GraphQL documents only; " +
  "minified or renamed tags are not yet fully resolved, so these counts are a floor, not a ceiling.";

// The GraphQL finding's typed attribute bag: `kind` drives grouping, `name`/`fields`/
// `on_type` drive the row. Read defensively from the untyped `Record<string, unknown>`
// so a malformed attribute can't crash the page (real-data-only — no faked fallbacks).
interface GqlAttributes {
  kind: string | null;
  name: string | null;
  fields: string[];
  onType: string | null;
}
function gqlAttributes(finding: Finding): GqlAttributes {
  const a = finding.attributes;
  const rawFields = a.fields;
  return {
    kind: typeof a.kind === "string" ? a.kind : null,
    name: typeof a.name === "string" ? a.name : null,
    fields: Array.isArray(rawFields) ? rawFields.filter((x): x is string => typeof x === "string") : [],
    onType: typeof a.on_type === "string" ? a.on_type : null,
  };
}

// The first sighting's JS location as a jump-to-Sources control, matching how the
// Findings drawer links an occurrence. Rendered only when the occurrence carries a
// location to open; `occurrence` is already non-null here (guarded by the caller).
function SourceLink({ occurrence, onJumpToSource }: {
  occurrence: Occurrence;
  onJumpToSource: (jump: SourceJump) => void;
}) {
  const where = occurrence.asset_url ?? occurrence.source_path;
  if (!where) return null;
  const location = `${where}${occurrence.line != null ? `:${occurrence.line}` : ""}`;
  return (
    <button
      type="button"
      className="gql-src"
      aria-label={`Open ${where} in Sources`}
      onClick={() =>
        onJumpToSource({ sourcePath: occurrence.source_path, assetUrl: occurrence.asset_url, line: occurrence.line })
      }
    >
      {location}
    </button>
  );
}

// One operation/fragment row: its name (or raw value), its top-level fields, the
// `on ...` target type for a fragment, and its first sighting's source location.
function GraphQLOperationRow({ finding, onJumpToSource }: {
  finding: Finding;
  onJumpToSource: (jump: SourceJump) => void;
}) {
  const { name, fields, onType } = gqlAttributes(finding);
  const label = name ?? finding.value ?? "(anonymous)";
  const occurrence = finding.occurrences[0] ?? null;
  return (
    <li className="gql-row">
      <div className="gql-row-head">
        <span className="gql-name">{label}</span>
        {onType && <span className="gql-on">on {onType}</span>}
      </div>
      {fields.length > 0 && <div className="gql-fields">{fields.join(", ")}</div>}
      {occurrence && <SourceLink occurrence={occurrence} onJumpToSource={onJumpToSource} />}
    </li>
  );
}

export function GraphQLPage({ data, onJumpToSource }: {
  data: FindingsResponse;
  onJumpToSource: (jump: SourceJump) => void;
}) {
  const operations = data.findings.filter((f) => f.type === "graphql");
  const groups = KIND_ORDER
    .map((kind) => ({ kind, items: operations.filter((f) => gqlAttributes(f).kind === kind) }))
    .filter((group) => group.items.length > 0);

  return (
    <div className="card">
      <div className="gql-head">
        <h2 className="rp-title">GraphQL</h2>
        {operations.length > 0 && (
          <span className="gql-total">{operations.length} total</span>
        )}
      </div>
      <p className="gql-note muted">{DETECTION_NOTE}</p>
      {operations.length === 0 ? (
        <p className="muted">No GraphQL operations recovered.</p>
      ) : (
        groups.map((group) => (
          <section key={group.kind} className="gql-group">
            <h3 className="gql-group-title">
              <span className="gql-group-name">{KIND_LABELS[group.kind]}</span>
              <span className="gql-group-count">{group.items.length}</span>
            </h3>
            <ul className="gql-list">
              {group.items.map((finding) => (
                <GraphQLOperationRow
                  key={finding.finding_hash}
                  finding={finding}
                  onJumpToSource={onJumpToSource}
                />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
