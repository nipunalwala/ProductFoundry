"use client";

import { useState } from "react";

import type { Approve, CompetitorList } from "@/lib/api";
import { competitorApproval } from "@/lib/edits";
import type { NewCompetitor } from "@/lib/edits";

const EMPTY: NewCompetitor = { name: "", url: "", positioning: "", target_users: "" };

/** The competitor list. While the checkpoint is open the user can remove and add, then approve. */
export function CompetitorCheckpoint({
  output,
  open,
  onApprove,
}: {
  output: CompetitorList;
  open: boolean;
  onApprove: (body?: Approve) => Promise<void>;
}) {
  const [removed, setRemoved] = useState<string[]>([]);
  const [added, setAdded] = useState<NewCompetitor[]>([]);
  const [draft, setDraft] = useState<NewCompetitor>(EMPTY);
  const [problem, setProblem] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  function toggle(id: string) {
    setRemoved((ids) => (ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id]));
  }

  function addDraft() {
    if (!draft.name.trim() || !/^https?:\/\/\S+$/.test(draft.url.trim())) {
      setProblem("A competitor needs a name and a website starting with http:// or https://.");
      return;
    }
    if (!draft.positioning.trim() || !draft.target_users.trim()) {
      setProblem("Say what the competitor is and who it is for.");
      return;
    }
    setAdded((items) => [...items, draft]);
    setDraft(EMPTY);
    setProblem(null);
  }

  async function approve() {
    setSending(true);
    setProblem(null);
    try {
      await onApprove(competitorApproval(removed, added));
    } catch (error) {
      setProblem(error instanceof Error ? error.message : "The approval failed.");
      setSending(false);
    }
  }

  const field = (key: keyof NewCompetitor, label: string, placeholder: string) => (
    <label>
      {label}
      <input
        value={draft[key]}
        placeholder={placeholder}
        onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
      />
    </label>
  );

  return (
    <section aria-label="Competitors">
      <h2>Competitors</h2>
      {open ? <p>Remove what is not a real competitor, add what is missing, then approve.</p> : null}
      <ul className="cards">
        {output.competitors.map((competitor) => {
          const gone = removed.includes(competitor.id);
          return (
            <li key={competitor.id} className={gone ? "card removed" : "card"}>
              <h3>
                {competitor.name}
                {competitor.is_incumbent ? <span className="tag">incumbent</span> : null}
              </h3>
              <p>{competitor.positioning}</p>
              <p className="muted">For: {competitor.target_users}</p>
              <p className="muted">Why: {competitor.reason}</p>
              <p>
                <a href={competitor.url} target="_blank" rel="noreferrer">
                  {competitor.url}
                </a>
              </p>
              {open ? (
                <button type="button" onClick={() => toggle(competitor.id)}>
                  {gone ? `Keep ${competitor.name}` : `Remove ${competitor.name}`}
                </button>
              ) : null}
            </li>
          );
        })}
        {added.map((competitor, index) => (
          <li key={`new-${index}`} className="card added">
            <h3>
              {competitor.name}
              <span className="tag">added by you</span>
            </h3>
            <p>{competitor.positioning}</p>
            <p className="muted">For: {competitor.target_users}</p>
            <button
              type="button"
              onClick={() => setAdded((items) => items.filter((_, i) => i !== index))}
            >
              Undo
            </button>
          </li>
        ))}
      </ul>

      {output.rejected.length > 0 ? (
        <details>
          <summary>{output.rejected.length} candidates were rejected</summary>
          <ul>
            {output.rejected.map((candidate) => (
              <li key={candidate.name}>
                <strong>{candidate.name}</strong>: {candidate.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      {open ? (
        <>
          <fieldset>
            <legend>Add a competitor</legend>
            {field("name", "Name", "Tricount")}
            {field("url", "Website", "https://www.tricount.com")}
            {field("positioning", "What it is", "Splits group expenses for trips")}
            {field("target_users", "Who it is for", "Friends travelling together")}
            <button type="button" onClick={addDraft}>
              Add to the list
            </button>
          </fieldset>
          {problem ? <p className="banner failed">{problem}</p> : null}
          <button type="button" className="primary" onClick={approve} disabled={sending}>
            {sending ? "Approving…" : "Approve competitors"}
          </button>
        </>
      ) : null}
    </section>
  );
}
