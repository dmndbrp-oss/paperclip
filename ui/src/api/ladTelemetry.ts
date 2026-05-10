import { api } from "./client";

export type TelemetryMetricSeries = {
  key: string;
  rows: Array<{ ts: string; value: number }>;
};

export type AgentMetricsResponse = {
  ladId: string;
  agentId: string;
  series: TelemetryMetricSeries[];
};

export const ladTelemetryApi = {
  getAgentMetrics: (ladId: string, agentId: string, since?: Date) => {
    const params = new URLSearchParams({ agentId });
    if (since) params.set("since", since.toISOString());
    return api.get<AgentMetricsResponse>(
      `/local-adapter-daemons/${ladId}/agent-metrics?${params}`,
    );
  },
};

// Fixture data for dashboard "renders against fixture data" acceptance criterion.
export function makeFixtureMetrics(): AgentMetricsResponse {
  const now = Date.now();
  const points = 12;
  const intervalMs = 5 * 60 * 1000;

  function series(key: string, gen: (i: number) => number): TelemetryMetricSeries {
    return {
      key,
      rows: Array.from({ length: points }, (_, i) => ({
        ts: new Date(now - (points - 1 - i) * intervalMs).toISOString(),
        value: gen(i),
      })),
    };
  }

  return {
    ladId: "fixture",
    agentId: "fixture",
    series: [
      series("idle_time_pct", (i) => 20 + Math.sin(i * 0.5) * 15 + Math.random() * 5),
      series("model_swap_count", (i) => Math.floor(Math.random() * 4)),
      series("queue_empty_events", (i) => Math.floor(Math.random() * 8)),
      series("manager_response_latency_ms", (i) => 120 + Math.sin(i * 0.3) * 80 + Math.random() * 30),
    ],
  };
}
