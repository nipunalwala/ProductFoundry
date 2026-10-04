// The backend API. Types come from lib/api-types.ts, generated from backend/openapi.json
// with `npm run gen:api`. Requests go to /api, which next.config.ts forwards to the backend.

import type { components } from "./api-types";

type Schemas = components["schemas"];
export type RunInput = Schemas["RunInput"];
export type RunSummary = Schemas["RunSummary"];
export type RunView = Schemas["RunView"];
export type StageView = Schemas["StageView"];
export type StageOutputView = Schemas["StageOutputView"];
export type Approve = Schemas["Approve"];
export type Competitor = Schemas["Competitor"];
export type CompetitorList = Schemas["CompetitorList"];

export type ExportName = "report" | "prd" | "tasks";
export type ExportFormat = "md" | "json";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // The body was not JSON; the status text is all there is.
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

export function exportUrl(runId: string, name: ExportName, format: ExportFormat): string {
  return `${BASE}/runs/${runId}/exports/${name}?format=${format}`;
}

export const api = {
  listRuns: () => request<RunSummary[]>("/runs"),
  getRun: (runId: string) => request<RunView>(`/runs/${runId}`),
  createRun: (input: RunInput) => post<RunView>("/runs", { input, seed: 0 }),
  stageOutput: (runId: string, stage: string) =>
    request<StageOutputView>(`/runs/${runId}/stages/${stage}`),
  approve: (runId: string, body?: Approve) => post<RunView>(`/runs/${runId}/approve`, body),
  resume: (runId: string, fromStage?: string) =>
    post<RunView>(`/runs/${runId}/resume`, { from_stage: fromStage ?? null }),
  exportJson: <T>(runId: string, name: ExportName) =>
    request<T>(`/runs/${runId}/exports/${name}?format=json`),
};
