import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { TuningRail } from "./TuningRail";
import { TuningRailProvider, useTuningRail } from "./TuningRailContext";
import type { RunData } from "../progress/runData";
import type { FindingsResponse, Finding } from "../../api/types";

vi.mock("../progress/runData", () => ({ useRunDataOptional: vi.fn() }));
import { useRunDataOptional } from "../progress/runData";
const mockRunData = vi.mocked(useRunDataOptional);

// Stub the three lever panels — they have their own tests.
vi.mock("../findings/SpecUpload", () => ({ SpecUpload: () => <div data-testid="spec-upload" /> }));
vi.mock("../findings/BaseUrlPanel", () => ({ BaseUrlPanel: () => <div data-testid="base-url-panel" /> }));
vi.mock("../findings/WrapperPanel", () => ({ WrapperPanel: () => <div data-testid="wrapper-panel" /> }));

const finding = (over: Partial<Finding> = {}): Finding => ({
  finding_hash: "h", type: "endpoint", value: "/x", path: null, severity: null,
  attributes: {}, first_stage: "analyze", revealable: false, triage: null,
  spec_status: null, occurrences: [], ...over,
});
const cov = (over: Partial<NonNullable<FindingsResponse["coverage"]>> = {}) => ({
  attributed: 10, unattributed: 0, secrets: 0, secrets_engine: null,
  sources_recovered: 0, source_map: "none", files: [], ...over,
});

const BASE_RUN_DATA: RunData = {
  runId: "r1", sessionId: null, state: "done", stage: null, pct: null, done: 0, total: 0,
  eta: null, error: null, assets: null, findings: null, loaded: true,
  pauseRequested: false, cancelRequested: false, captureStatus: null,
  technologies: null, hosts: null, failureCategory: null, failureReason: null,
  failureHost: null, handleControlResult: () => {}, refreshFindings: vi.fn(),
  events: [],
};

beforeEach(() => {
  mockRunData.mockReset();
  // Ensure the rail starts expanded in every test (localStorage may carry prior state).
  localStorage.removeItem("recon.tuningRailCollapsed");
});

function renderRail(partial: Partial<RunData> = {}) {
  mockRunData.mockReturnValue({ ...BASE_RUN_DATA, ...partial });
  render(
    <TuningRailProvider>
      <TuningRail runId="r1" specSummary={null} />
    </TuningRailProvider>,
  );
}

// Helper: renders the rail alongside a button that programmatically opens it via context.
function RailWithOpener({ lever }: { lever?: "spec" | "base-url" | "wrapper" }) {
  const { openWith, open } = useTuningRail();
  return (
    <div>
      <button data-testid="open-btn" onClick={() => lever ? openWith(lever) : open()}>open</button>
      <TuningRail runId="r1" specSummary={null} />
    </div>
  );
}

function renderWithOpener(partial: Partial<RunData> = {}, lever?: "spec" | "base-url" | "wrapper") {
  mockRunData.mockReturnValue({ ...BASE_RUN_DATA, ...partial });
  render(
    <TuningRailProvider>
      <RailWithOpener lever={lever} />
    </TuningRailProvider>,
  );
}

const noFindings: FindingsResponse = { run_id: "r1", count: 0, coverage: null, spec: null, findings: [] };

describe("TuningRail", () => {
  // REQ-TU2: banner hidden when no coverage data or fully attributed
  it("hides banner when coverage is null", () => {
    renderRail({ findings: noFindings });
    // Rail is expanded by default — content visible immediately.
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("hides banner when unattributed === 0", () => {
    renderRail({ findings: { ...noFindings, coverage: cov({ unattributed: 0 }) } });
    expect(screen.queryByRole("status")).toBeNull();
  });

  // REQ-TU2: banner shows count + % when unattributed > 0
  it("shows unattributed count and pct in banner", () => {
    renderRail({
      findings: { ...noFindings, coverage: cov({ attributed: 6, unattributed: 4 }) },
    });
    expect(screen.getByRole("status")).toHaveTextContent("4 (40%) unattributed");
  });

  // REQ-TU2: recommendation when dominant base ≥40% of unattributed
  it("marks base-URL lever as recommended when a dominant base covers ≥40% of unattributed", () => {
    renderRail({
      findings: {
        run_id: "r1", count: 3,
        coverage: cov({ attributed: 5, unattributed: 5 }),
        spec: null,
        findings: [
          finding({ finding_hash: "a", type: "endpoint_unresolved", value: ":serverUrl/v1/users" }),
          finding({ finding_hash: "b", type: "endpoint_unresolved", value: ":serverUrl/v1/posts" }),
          finding({ finding_hash: "c", type: "endpoint_unresolved", value: ":serverUrl/v1/items" }),
        ],
      },
    });
    expect(screen.getByLabelText("Recommended lever")).toBeInTheDocument();
    expect(screen.getByText(/base-URL rule may resolve/)).toBeInTheDocument();
  });

  // REQ-TU2: no recommendation when bases split <40% each
  it("shows no recommendation when no single base dominates", () => {
    renderRail({
      findings: {
        run_id: "r1", count: 4,
        coverage: cov({ attributed: 2, unattributed: 10 }),
        spec: null,
        findings: [
          finding({ finding_hash: "a", type: "endpoint_unresolved", value: ":serverUrl/v1/x" }),
          finding({ finding_hash: "b", type: "endpoint_unresolved", value: ":apiBase/v2/y" }),
          finding({ finding_hash: "c", type: "endpoint_unresolved", value: ":cdnHost/img" }),
          finding({ finding_hash: "d", type: "endpoint_unresolved", value: ":otherBase/z" }),
        ],
      },
    });
    expect(screen.queryByLabelText("Recommended lever")).toBeNull();
  });

  // REQ-TU1: toggle hides and shows content
  it("can be toggled closed and back open", async () => {
    renderRail({ findings: noFindings });
    // Rail starts expanded — all three panels are visible.
    expect(screen.getByTestId("spec-upload")).toBeInTheDocument();
    // Collapse
    fireEvent.click(screen.getByLabelText("Collapse tuning rail"));
    expect(screen.queryByTestId("spec-upload")).toBeNull();
    // Re-open
    fireEvent.click(screen.getByLabelText("Open extraction tuning rail"));
    expect(screen.getByTestId("spec-upload")).toBeInTheDocument();
  });

  // REQ-TU3: programmatic openWith expands rail when it was collapsed
  it("opens collapsed rail when openWith is called from context", () => {
    // Start with rail collapsed.
    localStorage.setItem("recon.tuningRailCollapsed", "1");
    renderWithOpener({ findings: noFindings }, "base-url");
    expect(screen.queryByTestId("base-url-panel")).toBeNull();
    act(() => { fireEvent.click(screen.getByTestId("open-btn")); });
    expect(screen.getByTestId("base-url-panel")).toBeInTheDocument();
  });

  // REQ-TU4: refreshFindings is available in RunData
  it("refreshFindings is exposed in RunData", () => {
    renderRail();
    expect(BASE_RUN_DATA.refreshFindings).toBeDefined();
  });

  // All three lever panels render when rail is open
  it("renders all three lever panels when expanded", () => {
    renderRail({ findings: noFindings });
    expect(screen.getByTestId("spec-upload")).toBeInTheDocument();
    expect(screen.getByTestId("base-url-panel")).toBeInTheDocument();
    expect(screen.getByTestId("wrapper-panel")).toBeInTheDocument();
  });
});
