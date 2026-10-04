"use client";

import { useState } from "react";

import type { Approve } from "@/lib/api";
import { NO_CHANGES, painPointApproval, painPointProblem } from "@/lib/edits";
import type { PainPointChanges } from "@/lib/edits";
import type { ReportExport } from "@/lib/exports";

import { QuoteBlock } from "./QuoteBlock";

/** The ranked pain points with counts and quotes. While the checkpoint is open the user can
 * rename, merge and drop, then approve. */
export function PainPointCheckpoint({
  report,
  open,
  onApprove,
}: {
  report: ReportExport;
  open: boolean;
  onApprove: (body?: Approve) => Promise<void>;
}) {
  const [changes, setChanges] = useState<PainPointChanges>(NO_CHANGES);
  const [problem, setProblem] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const counts = report.language_counts;
  const labels = Object.fromEntries(report.pain_points.map((p) => [p.cluster_id, p.label]));

  function rename(cluster: string, label: string) {
    setChanges((c) => ({ ...c, renames: { ...c.renames, [cluster]: label } }));
  }

  function toggleDrop(cluster: string) {
    setChanges((c) => ({
      ...c,
      dropped: c.dropped.includes(cluster)
        ? c.dropped.filter((item) => item !== cluster)
        : [...c.dropped, cluster],
    }));
  }

  function mergeInto(cluster: string, target: string) {
    setChanges((c) => {
      const next = { ...c.mergeInto };
      if (target) next[cluster] = target;
      else delete next[cluster];
      return { ...c, mergeInto: next };
    });
  }

  async function approve() {
    const invalid = painPointProblem(changes);
    if (invalid) {
      setProblem(invalid);
      return;
    }
    setSending(true);
    setProblem(null);
    try {
      await onApprove(painPointApproval(changes, labels));
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The approval failed.");
      setSending(false);
    }
  }

  return (
    <section aria-label="Pain points">
      <h2>Pain points</h2>
      <p>
        {counts.english + counts.hinglish + counts.not_analysed} reviews: {counts.english}{" "}
        English, {counts.hinglish} Hinglish, {counts.not_analysed} not analysed.{" "}
        {report.clustered_reviews} negative or mixed reviews were grouped into themes;{" "}
        {report.noise_reviews} fitted no theme.
      </p>
      <p className="muted">Ranking: {report.ranking_formula}.</p>
      {open ? <p>Rename, merge or drop pain points, then approve.</p> : null}
      {report.pain_points.length === 0 ? (
        <p>No pain points: there were not enough negative reviews to form a theme.</p>
      ) : null}

      <ol className="cards">
        {report.pain_points.map((point) => {
          const dropped = changes.dropped.includes(point.cluster_id);
          const target = changes.mergeInto[point.cluster_id] ?? "";
          const others = report.pain_points.filter((p) => p.cluster_id !== point.cluster_id);
          return (
            <li key={point.cluster_id} className={dropped || target ? "card removed" : "card"}>
              <h3>
                {point.rank}. {point.label}
              </h3>
              <p className="facts">
                Severity {point.severity}/5 · {point.review_count} reviews ·{" "}
                {Math.round(point.negative_share * 100)}% negative · score {point.score}
                {point.merged_cluster_ids.length > 0 ? " · merged" : ""}
              </p>
              <p>{point.description}</p>
              <p className="muted">Why this severity: {point.severity_reason}</p>
              <p className="muted">Products: {point.products.join(", ")}</p>
              <details>
                <summary>{point.quotes.length} quotes</summary>
                {point.quotes.map((quote) => (
                  <QuoteBlock key={quote.review_id} quote={quote} />
                ))}
              </details>
              {open ? (
                <div className="edits">
                  <label>
                    Label for pain point {point.rank}
                    <input
                      value={changes.renames[point.cluster_id] ?? point.label}
                      onChange={(e) => rename(point.cluster_id, e.target.value)}
                    />
                  </label>
                  <label>
                    Merge pain point {point.rank} into
                    <select
                      value={target}
                      onChange={(e) => mergeInto(point.cluster_id, e.target.value)}
                    >
                      <option value="">Keep separate</option>
                      {others.map((other) => (
                        <option key={other.cluster_id} value={other.cluster_id}>
                          {other.rank}. {other.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="inline">
                    <input
                      type="checkbox"
                      checked={dropped}
                      onChange={() => toggleDrop(point.cluster_id)}
                    />
                    Drop pain point {point.rank}
                  </label>
                </div>
              ) : null}
            </li>
          );
        })}
      </ol>

      {report.junk_clusters.length > 0 ? (
        <details>
          <summary>{report.junk_clusters.length} groups are not a pain point</summary>
          <ul>
            {report.junk_clusters.map((junk) => (
              <li key={junk.cluster_id}>
                {junk.review_count} reviews: {junk.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {open ? (
        <>
          {problem ? <p className="banner failed">{problem}</p> : null}
          <button type="button" className="primary" onClick={approve} disabled={sending}>
            {sending ? "Approving…" : "Approve pain points"}
          </button>
        </>
      ) : null}
    </section>
  );
}
