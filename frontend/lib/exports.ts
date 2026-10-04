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

export type ExportedPainPoint = Omit<Schemas["PainPoint"], "quote_review_ids" | "quote_glosses"> & {
  products: string[];
  quotes: Quote[];
};

export type ReportExport = {
  title: string;
  ranking_formula: string;
  language_counts: Schemas["LanguageCounts"];
  clustered_reviews: number;
  noise_reviews: number;
  pain_points: ExportedPainPoint[];
  junk_clusters: Schemas["JunkCluster"][];
};

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
