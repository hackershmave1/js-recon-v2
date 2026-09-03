import { TERMINAL_STATES } from "../../api/types";

// D54: decide whether to fire the "run finished" browser Notification. Pure so the tricky
// transition rule is unit-tested: fire only on the transition INTO a terminal state (not on
// every terminal re-render, not while already terminal), only if permission is granted, and
// only if the tab is hidden (a watching operator doesn't need it).
export function shouldNotifyRunFinished(
  was: string,
  now: string,
  ctx: { permission: string; hidden: boolean },
): boolean {
  if (was === now || TERMINAL_STATES.has(was) || !TERMINAL_STATES.has(now)) return false;
  return ctx.permission === "granted" && ctx.hidden;
}
