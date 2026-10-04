// Invented data in the shapes the backend returns. No real review is quoted here.

import type { CompetitorList, RunView } from "@/lib/api";
import type { PrdExport, Quote, ReportExport, TasksExport } from "@/lib/exports";

const STAGES = [
  "s1_competitors",
  "s2_reviews",
  "s3_pain_points",
  "s4_prd",
  "s5_tasks",
  "s6_roadmap",
  "s7_acceptance",
];

export function run(overrides: Partial<RunView> = {}, statuses: string[] = []): RunView {
  return {
    id: "run_1",
    status: "running",
    idea: "A simpler bill-splitting app",
    created_at: "2026-10-04T10:00:00Z",
    updated_at: "2026-10-04T10:05:00Z",
    seed: 0,
    error: null,
    pause_reason: null,
    awaiting_approval: null,
    input: {
      schema_version: 1,
      mode: "alternative",
      idea: "A simpler bill-splitting app",
      target_users: "Flatmates",
      platforms: ["android"],
      region: "IN",
      incumbent: { name: "Splitly", urls: [], store_ids: { google_play: null, app_store: null } },
      known_competitors: [],
      tech_stack: [],
    },
    stages: STAGES.map((key, index) => ({
      key,
      number: index + 1,
      status: (statuses[index] ?? "pending") as RunView["stages"][number]["status"],
      checkpoint: index === 0 || index === 2,
      has_output: false,
      edited: false,
      schema_version: null,
      error: null,
      started_at: null,
      finished_at: null,
      approved_at: null,
    })),
    ...overrides,
  };
}

export const COMPETITORS: CompetitorList = {
  schema_version: 1,
  competitors: [
    {
      id: "prod_a",
      name: "Splitly",
      url: "https://splitly.example",
      positioning: "Tracks shared expenses.",
      target_users: "Flatmates",
      store_ids: { google_play: null, app_store: null },
      reason: "The incumbent named in the run input.",
      is_incumbent: true,
    },
    {
      id: "prod_b",
      name: "Tabby",
      url: "https://tabby.example",
      positioning: "Splits restaurant bills.",
      target_users: "Friends eating out",
      store_ids: { google_play: null, app_store: null },
      reason: "Does the same job.",
      is_incumbent: false,
    },
  ],
  rejected: [{ name: "Ledger Weekly", url: null, reason: "A newsletter, not a product." }],
};

const QUOTE: Quote = {
  review_id: "rev_1",
  text: "payment fail ho gaya lekin paisa kat gaya",
  language: "hinglish",
  translation: "The payment failed but the money was deducted.",
  product: "Splitly",
  source: "google_play",
  url: "https://play.google.com/store/apps/details?id=app.example&reviewId=r1",
  date: "2026-08-01",
  rating: 1,
};

function point(rank: number, cluster: string, label: string): ReportExport["pain_points"][number] {
  return {
    cluster_id: cluster,
    merged_cluster_ids: [],
    rank,
    label,
    description: `${label}, according to the reviews.`,
    severity: 4,
    severity_reason: "A main task is blocked.",
    review_count: 20 - rank,
    negative_share: 0.75,
    product_ids: ["prod_a"],
    score: 14,
    products: ["Splitly"],
    quotes: [{ ...QUOTE, review_id: `rev_${rank}` }],
    // Pain point 1 doubles its share; March had too few reviews to say anything.
    trend: {
      months: ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"].map(
        (month, index) => {
          const thin = month === "2026-03";
          const reviews = thin ? 0 : rank === 1 && index >= 3 ? 8 : 4 - rank + 1;
          const total = thin ? 2 : 40;
          return {
            month,
            reviews,
            total_reviews: total,
            enough: !thin,
            share: thin ? null : reviews / total,
          };
        },
      ),
      recent_share: rank === 1 ? 0.2 : 0.05,
      previous_share: rank === 1 ? 0.1 : 0.05,
      growth: rank === 1 ? 1 : 0,
      rising: rank === 1,
    },
    switching:
      rank === 1
        ? [
            {
              ...QUOTE,
              review_id: "rev_switch",
              text: "payment failed again so I am switching to Tabby",
              language: "en",
              translation: null,
              intent: "leaving",
              other_product: "Tabby",
              reason: "payments keep failing",
            },
          ]
        : [],
  };
}

export const REPORT: ReportExport = {
  title: "Splitly",
  ranking_formula: "score = review_count x (0.5 + 0.5 x negative_share) x severity / 5",
  language_counts: { english: 29, hinglish: 14, not_analysed: 1 },
  clustered_reviews: 38,
  noise_reviews: 2,
  pain_points: [
    point(1, "cl_a", "Payments fail after money is debited"),
    point(2, "cl_b", "Daily limit on expenses"),
    point(3, "cl_c", "Expense limit paywall"),
  ],
  junk_clusters: [{ cluster_id: "cl_z", review_count: 9, reason: "The reviews are vague." }],
  trend_settings: {
    months: 6,
    window_months: 3,
    min_month_reviews: 5,
    min_window_reviews: 15,
    rising_threshold: 0.25,
  },
  switching_table: [
    {
      from_product: "Splitly",
      to_product: "Tabby",
      count: 2,
      reasons: ["payments keep failing"],
      review_ids: ["rev_switch", "rev_other"],
    },
    { from_product: "Splitly", to_product: null, count: 1, reasons: [], review_ids: ["rev_x"] },
  ],
  switching_review_count: 3,
};

export const PRD: PrdExport = {
  title: "A simpler bill-splitting app",
  schema_version: 1,
  problem: "Payments fail after the money is debited.",
  users: "Flatmates who pay each other by UPI.",
  goals: ["A payment always ends in a clear state."],
  non_goals: ["Recurring expenses."],
  requirements: [
    {
      id: "req_001",
      statement: "The product must refund a failed payment automatically.",
      priority: "must",
      evidence: ["cl_a"],
    },
    {
      id: "req_002",
      statement: "The product could settle a group in one tap.",
      priority: "could",
      evidence: ["gap_b", "cl_a"],
    },
  ],
  success_metrics: ["Share of payments that end in a final state."],
  evidence: [
    {
      id: "cl_a",
      kind: "pain_point",
      label: "Payments fail after money is debited",
      description: "A payment fails although the money left the account.",
      severity: 5,
      review_count: 16,
      negative_share: 0.75,
      quote: QUOTE,
    },
    { id: "gap_b", kind: "market_gap", product: "Tabby", fact: "Splits restaurant bills." },
  ],
};

export const TASKS: TasksExport = {
  title: "A simpler bill-splitting app",
  epics: [
    {
      id: "epic_01",
      title: "Reliable payments",
      description: "A payment always ends in a state the user can see.",
      tasks: [
        {
          id: "task_001",
          title: "Refund failed payments",
          description: "Start the refund when the provider reports a failure.",
          requirement_ids: ["req_001"],
          depends_on: ["task_002"],
          effort: "L",
          acceptance_criteria: [
            {
              kind: "happy_path",
              given: "a failed payment",
              when: "the failure is received",
              then: "a refund is started",
            },
            {
              kind: "failure_state",
              given: "a refund the provider rejects",
              when: "the rejection is received",
              then: "the refund is tried again",
            },
          ],
        },
        {
          id: "task_002",
          title: "Track the state of every payment",
          description: "Record each payment as pending, completed, failed or refunded.",
          requirement_ids: ["req_001"],
          depends_on: [],
          effort: "M",
        },
      ],
    },
  ],
  build_order: ["task_002", "task_001"],
  requirements: { req_001: "The product must refund a failed payment automatically." },
};
