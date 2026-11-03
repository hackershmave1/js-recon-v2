import type { LastCall } from "../../api/types";
import { describeRun } from "./describeRun";

export function RunDetails({ call }: { call: LastCall | null | undefined }) {
  if (!call) return null;
  return <p className="tm-run-details muted">{describeRun(call)}</p>;
}
