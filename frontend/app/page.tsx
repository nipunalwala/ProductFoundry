"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { RunSummary } from "@/lib/api";
import { STATUS_NAMES } from "@/lib/edits";

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    api
      .listRuns()
      .then(setRuns)
      .catch((error: Error) => setFailure(error.message));
  }, []);

  return (
    <>
      <h1>Runs</h1>
      <p>
        <Link href="/new" className="button primary">
          New run
        </Link>
      </p>
      {failure ? (
        <p className="banner failed">
          The backend could not be reached ({failure}). Start it with{" "}
          <code>productfoundry serve</code>.
        </p>
      ) : null}
      {runs && runs.length === 0 ? <p>No runs yet.</p> : null}
      {runs && runs.length > 0 ? (
        <ul className="cards">
          {runs.map((run) => (
            <li key={run.id} className="card">
              <h3>
                <Link href={`/runs/${run.id}`}>{run.idea}</Link>
              </h3>
              <p className="facts">
                <span className={`status ${run.status}`}>
                  {STATUS_NAMES[run.status] ?? run.status}
                </span>{" "}
                · started {new Date(run.created_at).toLocaleString()}
              </p>
            </li>
          ))}
        </ul>
      ) : null}
      {!runs && !failure ? <p>Loading…</p> : null}
    </>
  );
}
