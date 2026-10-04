// What the user changed at a checkpoint, turned into the body of POST /runs/{id}/approve.
// Pure, so it is tested without a browser. Undefined means "approve unchanged".

import type { Approve } from "./api";

export type NewCompetitor = {
  name: string;
  url: string;
  positioning: string;
  target_users: string;
};

export function competitorApproval(
  removedIds: string[],
  added: NewCompetitor[],
): Approve | undefined {
  if (removedIds.length === 0 && added.length === 0) return undefined;
  return { competitors: { remove: removedIds, add: added } };
}

export type PainPointChanges = {
  renames: Record<string, string>; // cluster id -> new label
  dropped: string[]; // cluster ids
  mergeInto: Record<string, string>; // cluster id -> the cluster id it is folded into
};

export const NO_CHANGES: PainPointChanges = { renames: {}, dropped: [], mergeInto: {} };

/** Why the changes cannot be sent, or null. */
export function painPointProblem(changes: PainPointChanges): string | null {
  for (const [source, target] of Object.entries(changes.mergeInto)) {
    if (changes.dropped.includes(source) || changes.dropped.includes(target)) {
      return "A pain point cannot be both dropped and merged.";
    }
    if (target in changes.mergeInto) {
      return "Merge into a pain point that is not itself being merged.";
    }
  }
  return null;
}

export function painPointApproval(
  changes: PainPointChanges,
  originalLabels: Record<string, string>,
): Approve | undefined {
  const rename: Record<string, string> = {};
  for (const [cluster, label] of Object.entries(changes.renames)) {
    const merged = cluster in changes.mergeInto || changes.dropped.includes(cluster);
    if (!merged && label.trim() && label.trim() !== originalLabels[cluster]) {
      rename[cluster] = label.trim();
    }
  }
  // One group per target: the target first, then everything folded into it.
  const groups: Record<string, string[]> = {};
  for (const [source, target] of Object.entries(changes.mergeInto)) {
    (groups[target] ??= [target]).push(source);
  }
  const merge = Object.values(groups);
  if (Object.keys(rename).length === 0 && merge.length === 0 && changes.dropped.length === 0) {
    return undefined;
  }
  return { pain_points: { rename, merge, drop: changes.dropped, rank: [] } };
}

export const STAGE_NAMES: Record<string, string> = {
  s1_competitors: "Competitor research",
  s2_reviews: "Review collection",
  s3_pain_points: "Pain points",
  s4_prd: "PRD",
  s5_tasks: "Task breakdown",
  s6_roadmap: "Roadmap",
  s7_acceptance: "Acceptance criteria",
};

export const STATUS_NAMES: Record<string, string> = {
  pending: "Waiting",
  running: "Running",
  awaiting_approval: "Needs your review",
  paused_quota: "Paused",
  failed: "Failed",
  completed: "Done",
};
