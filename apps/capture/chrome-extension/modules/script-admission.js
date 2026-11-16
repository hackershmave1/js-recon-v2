// script-admission.js — the ordered rule for ONE script request (ADR-0010). Pure: the worker gathers
// the facts and this decides, so a Node test covers each rule.
//
// In order:
//   1. the page the tab is on is outside the capture's scope  -> IGNORE_PAGE (the tab is not ours)
//   2. a known third-party file, or a known third-party host the researcher did not scope
//                                                              -> DROP_THIRD_PARTY
//   3. the script's host is in the capture's scope             -> CAPTURE (analyzed as before)
//   4. the script's site is on the ignore list                 -> DROP_IGNORED (never fetched)
//   5. the page could not be determined (worker, tabId -1)     -> DROP_UNKNOWN_PAGE (never quarantined)
//   6. anything else                                           -> QUARANTINE (held, not analyzed)
// "Capture every tab" is the researcher saying everything is in: CAPTURE.
//
// `pageKnown` is false when the top-frame URL could not be read; rule 1 then cannot apply, and a
// host-in-scope script is captured exactly as it was before the page-aware rule existed.
export const ADMIT = Object.freeze({
  CAPTURE: 'capture',
  IGNORE_PAGE: 'ignore_page',
  DROP_THIRD_PARTY: 'drop_third_party',
  DROP_IGNORED: 'drop_ignored',
  DROP_UNKNOWN_PAGE: 'drop_unknown_page',
  QUARANTINE: 'quarantine',
});

export function admitScript({
  captureEverything = false,
  pageKnown,
  pageInScope,
  thirdPartyFile = false,
  thirdPartyHost = false,
  scriptInScope,
  ignored = false,
}) {
  if (captureEverything) return ADMIT.CAPTURE;
  if (pageKnown && !pageInScope) return ADMIT.IGNORE_PAGE;
  // A host entry never drops a host the researcher typed into the scope list; a file pattern
  // (jquery.min.js, /wp-content/plugins/) applies on any host.
  if (thirdPartyFile || (thirdPartyHost && !scriptInScope)) return ADMIT.DROP_THIRD_PARTY;
  if (scriptInScope) return ADMIT.CAPTURE;
  if (ignored) return ADMIT.DROP_IGNORED;
  if (!pageKnown) return ADMIT.DROP_UNKNOWN_PAGE;
  return ADMIT.QUARANTINE;
}
