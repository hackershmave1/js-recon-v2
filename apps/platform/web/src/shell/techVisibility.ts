import { READABLE_STATES, type TechnologiesResponse } from "../api/types";

// Extension captures, uploads and single-URL runs never collect technology signals, so the Tech
// stack tab and the Overview tile could only ever say "not collected". Hide both once the run has
// finished without detection. While it is still running they stay ("awaiting analysis"), and a
// direct link to the tab still shows the explanation.
export function techStackHidden(technologies: TechnologiesResponse | null | undefined, state: string | null | undefined): boolean {
  return technologies?.detection_ran === false && !!state && READABLE_STATES.has(state);
}
