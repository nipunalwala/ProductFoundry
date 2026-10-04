import type { RunView } from "@/lib/api";
import { STAGE_NAMES, STATUS_NAMES } from "@/lib/edits";

/** The run's status, why it is paused or failed, and each stage's progress. */
export function StageProgress({ run, onResume }: { run: RunView; onResume?: () => void }) {
  return (
    <section aria-label="Progress">
      {run.status === "paused_quota" ? (
        <p className="banner paused" role="status">
          <strong>Paused, not failed.</strong> The free AI quota for today is used up
          {run.pause_reason ? ` (${run.pause_reason})` : ""}. The run continues by itself after
          the quota resets.
        </p>
      ) : null}
      {run.status === "failed" ? (
        <div className="banner failed" role="alert">
          <p>
            <strong>The run failed.</strong> {run.error}
          </p>
          {onResume ? (
            <button type="button" onClick={onResume}>
              Try again
            </button>
          ) : null}
        </div>
      ) : null}
      <ol className="stages">
        {run.stages.map((stage) => (
          <li key={stage.key} className={`stage ${stage.status}`}>
            <span className="stage-name">{STAGE_NAMES[stage.key] ?? stage.key}</span>
            <span className="stage-status">
              {STATUS_NAMES[stage.status] ?? stage.status}
              {stage.edited ? " · edited" : ""}
            </span>
            {stage.error ? <span className="stage-error">{stage.error}</span> : null}
          </li>
        ))}
      </ol>
    </section>
  );
}
