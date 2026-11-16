// One reviewable table of the fixed words the finding detail uses to explain a finding
// (L2 / F16). Nothing here is computed from a run: it describes what each finding TYPE is and
// the checks worth making next. Everything run-specific (confidence, severity reason, method,
// spec verdict) is derived in findingExplain.ts from data the API already returns.
// Keep next steps to 1-3 concrete actions; never promise an outcome the tool can't know.

export interface ExplainRow {
  /** One plain-language line: what this finding IS. */
  kind: string;
  /** What the type alone says about how sure the tool is (omit when the type says nothing). */
  certainty?: string;
  /** 1-3 concrete suggestions. */
  nextSteps: string[];
}

export const EXPLAIN_TABLE: Record<string, ExplainRow> = {
  endpoint: {
    kind: "An HTTP call the JavaScript makes to a backend path (a fetch, XHR or axios-style request).",
    certainty: "A resolved URL: static analysis read the path straight from the code.",
    nextSteps: [
      "Open the source at the line to see how the request is built.",
      "Check whether the call needs authentication (look for auth headers or tokens nearby).",
      "Probe it, or add it to your notes if it is not in your API spec.",
    ],
  },
  endpoint_suspected: {
    kind: "An inferred API: a path that looks like a backend route, recovered from a call whose URL was not fully resolved.",
    certainty: "Inferred from a path-shaped string at a generic call, not from a proven request, so it may not be a real route.",
    nextSteps: [
      "Open the source to confirm the string is passed to a network call.",
      "If it is real, probe it and compare with the resolved APIs.",
    ],
  },
  endpoint_unresolved: {
    kind: "A suspected call: a real network call whose URL is built at runtime, so no endpoint path is known.",
    certainty: "The call itself is real, but there is no static path: the URL is computed when the page runs.",
    nextSteps: [
      "Open the source at the line to see which values build the URL.",
      "Add a base URL if the missing part is a configured host or prefix.",
      "Capture the page with the extension to see the concrete URL at runtime.",
    ],
  },
  endpoint_generic: {
    kind: "A generic call from an older run: a request whose target the tool could not classify.",
    nextSteps: ["Re-run the analysis to get the current, more specific classification."],
  },
  page_route: {
    kind: "A page route: a client-side navigation path inside the app, not a backend API call.",
    nextSteps: [
      "Visit the page, signed in if it needs a login, to see which API calls it makes.",
      "Treat it as UI surface; look for matching API findings for the data it loads.",
    ],
  },
  param: {
    kind: "A request parameter (query, body, header or path value) one of the recovered calls sends.",
    nextSteps: [
      "Open the owning endpoint and see how the value is filled in.",
      "Try changing it with another valid value if it names an object or a privilege.",
    ],
  },
  secret: {
    kind: "A secret: a credential, token or key pattern found in the JavaScript or its recovered source. The value is redacted here.",
    nextSteps: [
      "Reveal it (audited) to see the value and judge whether it is a real credential.",
      "Open the source to see what the key is used for and which service it belongs to.",
      "If it is real, report it for rotation and note where it ships from.",
    ],
  },
  secret_suspected: {
    kind: "A suspected secret: a value that looks like a credential but matched a looser rule. Many of these are not real secrets.",
    certainty: "This lane trades precision for recall, so a good share of these are false positives.",
    nextSteps: [
      "Reveal it (audited) and check whether it is a public identifier, a hash or a real credential.",
      "Mark it false-positive in triage if it is not a secret.",
    ],
  },
  internal_ip: {
    kind: "An internal IP address (private range) written into the JavaScript, which hints at back-end network layout.",
    certainty: "Read directly from the code as a literal address; it is not proof the address is reachable.",
    nextSteps: [
      "Open the source to see what the address is used for.",
      "Note it as an information leak; it only matters if the address is reachable from where you test.",
    ],
  },
  graphql: {
    kind: "A GraphQL operation or fragment defined in the JavaScript, with the fields it asks for.",
    nextSteps: [
      "Open the source to read the full query text.",
      "Find the GraphQL endpoint in the API findings and try the operation against it.",
    ],
  },
  postmessage_sink: {
    kind: "A postMessage listener: code that receives messages from other windows. Informational, not a vulnerability by itself.",
    nextSteps: [
      "Open the source and check whether the handler verifies the sender's origin.",
      "See where the message data flows (DOM writes, navigation, storage).",
    ],
  },
  storage_sink: {
    kind: "A storage write: code that saves data to localStorage, sessionStorage or a cookie. Informational.",
    nextSteps: [
      "Open the source to see what is stored and whether it is sensitive (tokens, user data).",
      "Check whether the stored value is read back later and used unsafely.",
    ],
  },
};
