import { describe, it, expect, vi } from "vitest";
import { withExactLoc } from "../../api/testOccurrence";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TenantProvider } from "../../tenant/TenantContext";
import { FindingDetail } from "./FindingDetail";
import type { Finding, Occurrence, SourceJump } from "../../api/types";

const occ = (over: Partial<Occurrence> = {}): Occurrence => withExactLoc({
  host: null, raw_url: null, source_path: "app.js", line: 10, col: 4,
  offset_start: null, offset_end: null, evidence: null, engine: "vespasian",
  confidence: "high", verified: true, asset_url: null, ...over,
});
const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/api/users", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [occ()], ...over,
});
const show = (f: Finding, onJump: (j: SourceJump) => void = () => {}) =>
  render(<TenantProvider><FindingDetail finding={f} runId="r" onJumpToSource={onJump} /></TenantProvider>);

describe("FindingDetail", () => {
  it("renders non-secret evidence but redacts a secret's evidence", () => {
    show(finding({ occurrences: [occ({ evidence: "GET /api/users" })] }));
    expect(screen.getByText(/GET \/api\/users/)).toBeInTheDocument();
  });

  it("suppresses evidence for secret-type findings", () => {
    show(finding({ type: "secret", value: "aws:sha256:abcd", revealable: true,
      occurrences: [occ({ evidence: "LEAKED-SECRET-MARKER-XYZ" })] }));
    expect(screen.queryByText(/LEAKED-SECRET-MARKER-XYZ/)).toBeNull();
  });

  it("treats a suspected secret like a secret: evidence hidden + reveal offered", () => {
    // D33-B: secret_suspected rides the same isSecret path — its raw value is never
    // rendered and a revealable one still gets the audited reveal control.
    show(finding({ type: "secret_suspected", value: "config:sha256:abcd", revealable: true,
      occurrences: [occ({ evidence: "SUSPECTED-MARKER-XYZ" })] }));
    expect(screen.queryByText(/SUSPECTED-MARKER-XYZ/)).toBeNull();
    expect(screen.getByRole("button", { name: /reveal/i })).toBeInTheDocument();
  });

  it("renders a cleartext internal IP with its evidence and never a reveal button", () => {
    // internal_ip is a cleartext info-disclosure finding, NOT a secret: value + evidence
    // show verbatim and it must never ride the isSecret reveal path. revealable is forced
    // true to prove the isSecret exclusion (not a default flag) is what suppresses reveal.
    show(finding({ type: "internal_ip", value: "10.0.0.1", revealable: true,
      occurrences: [occ({ evidence: "fetch('http://10.0.0.1/admin')" })] }));
    expect(screen.getByText("10.0.0.1")).toBeInTheDocument();
    expect(screen.getByText(/fetch\('http:\/\/10\.0\.0\.1\/admin'\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /reveal/i })).toBeNull();
  });

  it("attributes an occurrence to its source asset when asset_url is present", () => {
    show(finding({ occurrences: [occ({ asset_url: "https://acme.io/app.js" })] }));
    expect(screen.getByText(/https:\/\/acme\.io\/app\.js/)).toBeInTheDocument();
  });

  it("shows the real bundle URL, not the input.js placeholder, when no source map recovered", () => {
    // The analyze stage stores source_path="input.js" (the bundle-wide fallback) when no
    // map recovered real paths, but the real bundle rides on asset_url. That bundle — not
    // the placeholder — must be the occurrence's source label (#2 source attribution).
    show(finding({ path: "input.js", value: "GET /api/x", occurrences: [occ({
      source_path: "input.js", line: 2521,
      asset_url: "https://app.attio.com/web-assets/main.bundle.b1f1c3869bb0e4ee.js" })] }));
    expect(screen.getByText(/main\.bundle\.b1f1c3869bb0e4ee\.js:2521/)).toBeInTheDocument();
    // "input.js" must not appear anywhere — not the occurrence label, not the header.
    expect(screen.queryByText(/input\.js/)).toBeNull();
  });

  it("jump-to-source of an input.js occurrence targets its real bundle", async () => {
    const onJump = vi.fn();
    show(finding({ occurrences: [occ({ source_path: "input.js", line: 3,
      asset_url: "https://acme.io/app.bundle.js" })] }), onJump);
    await userEvent.click(screen.getByRole("button", { name: /open https:\/\/acme\.io\/app\.bundle\.js in sources/i }));
    expect(onJump).toHaveBeenCalledWith(expect.objectContaining({ sourcePath: "input.js", assetUrl: "https://acme.io/app.bundle.js", line: 3 }));
  });

  it("makes an occurrence with a source location a jump-to-source button", async () => {
    const onJump = vi.fn();
    show(finding({ occurrences: [occ({ source_path: "app.js", line: 10, asset_url: "https://acme.io/app.js" })] }), onJump);
    await userEvent.click(screen.getByRole("button", { name: /open app\.js in sources/i }));
    expect(onJump).toHaveBeenCalledWith(expect.objectContaining({ sourcePath: "app.js", assetUrl: "https://acme.io/app.js", line: 10 }));
  });

  it("offers no jump when there is no exact location — never a guessed spot", () => {
    // e.g. a capture-body secret: it names a bundle but has no place in any served file.
    show(finding({ occurrences: [occ({ source_path: "input.js", line: 1, loc: null,
      asset_url: "https://acme.io/app.js" })] }), vi.fn());
    expect(screen.queryByRole("button", { name: /in sources/i })).toBeNull();
    expect(screen.getByText(/no exact location/)).toBeInTheDocument();
  });

  it("labels an old run's line as approximate", () => {
    show(finding({ occurrences: [occ({ source_path: "app.js", loc: null, approximate_line: 10 })] }), vi.fn());
    expect(screen.getByText(/app\.js:~10/)).toBeInTheDocument();
    expect(screen.getByText(/approximate location/)).toBeInTheDocument();
  });

  it("renders 'unclassified' when spec_status is null", () => {
    show(finding({ spec_status: null }));
    expect(screen.getByText("unclassified")).toHaveClass("chip-unclassified");
  });

  it("renders a distinct chip class for each real spec_status verdict", () => {
    show(finding({ spec_status: { status: "documented", reason: null, matched_operation: "GET /x" } }));
    expect(screen.getByText("documented")).toHaveClass("chip-documented");
    show(finding({ finding_hash: "h2", spec_status: { status: "shadow", reason: null, matched_operation: null } }));
    expect(screen.getByText("shadow")).toHaveClass("chip-shadow");
    show(finding({ finding_hash: "h3", spec_status: { status: "unresolved", reason: null, matched_operation: null } }));
    expect(screen.getByText("unresolved")).toHaveClass("chip-unresolved");
  });
  it("names the host of each occurrence, so a finding seen on two hosts shows both", () => {
    show(finding({ value: "/api/event", occurrences: [
      occ({ host: "app.acme.io", source_path: "app.js" }),
      occ({ host: "plausible.io", source_path: "vendor.js" }),
    ] }));
    expect(screen.getByText(/app\.acme\.io/)).toBeInTheDocument();
    expect(screen.getByText(/plausible\.io/)).toBeInTheDocument();
  });

  it("omits the host label for a relative (host-less) occurrence", () => {
    show(finding({ occurrences: [occ({ host: null })] }));
    expect(screen.queryByTestId("occ-host")).toBeNull();
  });

  describe("L2: explanation + merged secret occurrence", () => {
    const bundle = (over: Partial<Occurrence> = {}) =>
      occ({ source_path: "input.js", asset_url: "https://acme.io/app.js", evidence: null, ...over });
    const recovered = (over: Partial<Occurrence> = {}) =>
      occ({ source_path: "src/config.ts", asset_url: "https://acme.io/app.js", ...over });

    it("shows the four explanation sections in the drawer detail", () => {
      show(finding({ priority_reason: { base: 45, risk_tag: null, bump: 0, score: 45, label: "medium" }, severity: "medium" }));
      expect(screen.getByRole("region", { name: /what it is/i })).toBeInTheDocument();
      expect(screen.getByRole("region", { name: /why this severity/i })).toBeInTheDocument();
    });

    it("lists a secret seen in the bundle and in its recovered source as ONE occurrence", () => {
      show(finding({ type: "secret", value: "aws:sha256:ab", revealable: true,
        occurrences: [bundle(), recovered()] }));
      const items = screen.getAllByRole("listitem").filter((li) => li.closest("ul")?.classList.contains("fd-occs"));
      expect(items).toHaveLength(1);
      expect(items[0].textContent).toMatch(/src\/config\.ts/);
      expect(items[0].textContent).toMatch(/https:\/\/acme\.io\/app\.js/); // bundle kept as the owner tag
    });

    it("keeps both lines when the bundle sighting has no recovered twin (different assets)", () => {
      show(finding({ type: "secret", occurrences: [bundle(), recovered({ asset_url: "https://acme.io/other.js" })] }));
      expect(document.querySelectorAll(".fd-occs > li")).toHaveLength(2);
    });

    it("pairs one-to-one: two bundle sightings and one recovered leave two lines", () => {
      show(finding({ type: "secret", occurrences: [bundle(), bundle(), recovered()] }));
      expect(document.querySelectorAll(".fd-occs > li")).toHaveLength(2);
    });

    it("does not merge non-secret occurrences", () => {
      show(finding({ occurrences: [bundle(), recovered()] }));
      expect(document.querySelectorAll(".fd-occs > li")).toHaveLength(2);
    });
  });
});
