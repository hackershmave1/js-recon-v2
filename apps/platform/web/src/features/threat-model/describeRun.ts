import type { LastCall } from "../../api/types";

const KEY_LABEL: Record<string, string> = { session: "session key", team: "team key", env: "server key" };

function formatCost(costUsd: string | null): string {
  if (costUsd == null) return "cost unknown";
  const usd = Number(costUsd);
  return usd > 0 && usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${(ms / 1000).toFixed(1)} s`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  return `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
}

// One line of facts about the LLM call behind this threat model, all from the recorded call
// (never estimated): model, whose key paid, tokens in → out, cost, duration.
export function describeRun(call: LastCall): string {
  if (call.unavailable) return "Run details unavailable";
  const parts = [call.model];
  if (call.key_source) parts.push(KEY_LABEL[call.key_source] ?? call.key_source);
  if (call.prompt_tokens != null) {
    parts.push(`${call.prompt_tokens.toLocaleString()} → ${(call.completion_tokens ?? 0).toLocaleString()} tokens`);
  }
  parts.push(formatCost(call.cost_usd), formatDuration(call.latency_ms));
  return parts.join(" · ");
}
