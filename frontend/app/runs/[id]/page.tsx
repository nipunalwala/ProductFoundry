"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { CompetitorCheckpoint } from "@/components/CompetitorCheckpoint";
import { PainPointCheckpoint } from "@/components/PainPointCheckpoint";
import { PrdView } from "@/components/PrdView";
import { StageProgress } from "@/components/StageProgress";
import { TaskList } from "@/components/TaskList";
import { api, exportUrl } from "@/lib/api";
import type { Approve, CompetitorList, ExportName, RunView } from "@/lib/api";
import { STATUS_NAMES } from "@/lib/edits";
import type { PrdExport, ReportExport, TasksExport } from "@/lib/exports";

const POLL_MS = 2000;
const EXPORTS: { name: ExportName; label: string; stage: string }[] = [
  { name: "report", label: "Pain-point report", stage: "s3_pain_points" },
  { name: "prd", label: "PRD", stage: "s4_prd" },
  { name: "tasks", label: "Tasks", stage: "s5_tasks" },
];

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<RunView | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [competitors, setCompetitors] = useState<CompetitorList | null>(null);
  const [report, setReport] = useState<ReportExport | null>(null);
  const [prd, setPrd] = useState<PrdExport | null>(null);
  const [tasks, setTasks] = useState<TasksExport | null>(null);

  const load = useCallback(() => {
    api
      .getRun(id)
      .then((loaded) => {
        setRun(loaded);
        setFailure(null);
      })
      .catch((error: Error) => setFailure(error.message));
  }, [id]);

  useEffect(load, [load]);

  // While the worker is busy, ask again every two seconds.
  const busy = run?.status === "pending" || run?.status === "running";
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [busy, load]);

  // Fetch each output once its stage has one; fetch again when a stage changes.
  const progress = run?.stages.map((s) => `${s.key}:${s.status}:${s.edited}`).join("|") ?? "";
  useEffect(() => {
    if (!run) return;
    const has = (key: string) => run.stages.some((s) => s.key === key && s.has_output);
    const quiet = () => undefined; // an output that cannot be shown yet is simply not shown
    if (has("s1_competitors")) {
      api
        .stageOutput(run.id, "s1_competitors")
        .then((view) => setCompetitors(view.output as CompetitorList))
        .catch(quiet);
    }
    if (has("s3_pain_points")) {
      api.exportJson<ReportExport>(run.id, "report").then(setReport).catch(quiet);
    }
    if (has("s4_prd")) api.exportJson<PrdExport>(run.id, "prd").then(setPrd).catch(quiet);
    if (has("s5_tasks")) api.exportJson<TasksExport>(run.id, "tasks").then(setTasks).catch(quiet);
    // `progress` stands for the parts of `run` that matter here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress]);

  async function approve(body?: Approve) {
    setRun(await api.approve(id, body));
  }

  async function resume() {
    try {
      setRun(await api.resume(id));
      load();
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "The run could not be resumed.");
    }
  }

  if (failure && !run) return <p className="banner failed">{failure}</p>;
  if (!run) return <p>Loading…</p>;

  const done = (key: string) => run.stages.some((s) => s.key === key && s.has_output);
  return (
    <>
      <h1>{run.idea}</h1>
      <p className="facts">
        <span className={`status ${run.status}`}>{STATUS_NAMES[run.status] ?? run.status}</span>
        {run.input.incumbent ? ` · alternative to ${run.input.incumbent.name}` : ""} · region{" "}
        {run.input.region}
      </p>
      {failure ? <p className="banner failed">{failure}</p> : null}
      <StageProgress run={run} onResume={resume} />

      <section aria-label="Exports" className="exports">
        {EXPORTS.filter((item) => done(item.stage)).map((item) => (
          <span key={item.name}>
            {item.label}:{" "}
            <a href={exportUrl(run.id, item.name, "md")} download>
              Markdown
            </a>{" "}
            <a href={exportUrl(run.id, item.name, "json")} download>
              JSON
            </a>
          </span>
        ))}
      </section>

      {tasks ? <TaskList tasks={tasks} /> : null}
      {prd ? <PrdView prd={prd} /> : null}
      {report ? (
        <PainPointCheckpoint
          key={`report-${run.awaiting_approval}`}
          report={report}
          open={run.awaiting_approval === "s3_pain_points"}
          onApprove={approve}
        />
      ) : null}
      {competitors ? (
        <CompetitorCheckpoint
          key={`competitors-${run.awaiting_approval}`}
          output={competitors}
          open={run.awaiting_approval === "s1_competitors"}
          onApprove={approve}
        />
      ) : null}
    </>
  );
}
