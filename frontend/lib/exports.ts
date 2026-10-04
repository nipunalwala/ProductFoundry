// The JSON exports (GET /runs/{id}/exports/{name}?format=json). The backend builds them
// as plain data on top of the stage outputs, so their extra fields are typed here by hand;
// everything else comes from the generated schema types.

import type { components } from "./api-types";

type Schemas = components["schemas"];

export type Quote = {
  review_id: string;
  text: string;
  language: string | null;
  translation: string | null; // an English gloss of a Hinglish quote, never the review's own text
  product: string;
  source: string;
  url: string | null;
  date: string;
  rating: number | null;
};

// A month's share is null when the month had too few reviews to say: a gap, not a zero.
export type TrendMonth = Schemas["TrendPoint"] & { share: number | null };
export type ExportedTrend = Omit<Schemas["Trend"], "months"> & { months: TrendMonth[] };

// A review about switching products: the quote, and what the model read in it.
export type SwitchingQuote = Quote & Omit<Schemas["SwitchingReview"], "review_id">;

export type ExportedPainPoint = Omit<
  Schemas["PainPoint"],
  "quote_review_ids" | "quote_glosses" | "switching_review_ids" | "trend"
> & {
  products: string[];
  quotes: Quote[];
  trend: ExportedTrend;
  switching: SwitchingQuote[];
};

export type ReportExport = {
  title: string;
  ranking_formula: string;
  language_counts: Schemas["LanguageCounts"];
  clustered_reviews: number;
  noise_reviews: number;
  pain_points: ExportedPainPoint[];
  junk_clusters: Schemas["JunkCluster"][];
  trend_settings: Schemas["TrendSettings"];
  switching_table: Schemas["SwitchingRow"][];
  switching_review_count: number;
};

export const INTENT_NAMES: Record<string, string> = {
  leaving: "Leaving",
  switched_from: "Has left",
  switched_to: "Came from another product",
  considering: "Thinking of leaving",
};

/** One sentence on where a pain point is heading, or why that cannot be said. */
export function trendSentence(trend: ExportedTrend, window: number): string {
  if (trend.recent_share == null || trend.previous_share == null) {
    return "Not enough reviews in the last months to say whether this is growing.";
  }
  const recent = Math.round(trend.recent_share * 100);
  const previous = Math.round(trend.previous_share * 100);
  const growth =
    trend.growth == null ? "" : ` (${trend.growth > 0 ? "+" : ""}${Math.round(trend.growth * 100)}%)`;
  return `${recent}% of all reviews in the last ${window} months, ${previous}% in the ${window} before${growth}.`;
}

export type Evidence =
  | {
      id: string;
      kind: "pain_point";
      label: string;
      description: string;
      severity: number;
      review_count: number;
      negative_share: number;
      quote: Quote;
    }
  | { id: string; kind: "market_gap"; product: string; fact: string };

export type PrdExport = Omit<Schemas["Prd"], "market_gaps"> & {
  title: string;
  evidence: Evidence[];
};

export type ExportedTask = Schemas["Task"] & { acceptance_criteria?: Schemas["Criterion"][] };

export type TasksExport = {
  title: string;
  epics: (Omit<Schemas["Epic"], "tasks"> & { tasks: ExportedTask[] })[];
  build_order: string[];
  requirements: Record<string, string>;
};

export const SOURCE_NAMES: Record<string, string> = {
  google_play: "Google Play",
  app_store: "App Store",
  reddit: "Reddit",
  product_hunt: "Product Hunt",
};
