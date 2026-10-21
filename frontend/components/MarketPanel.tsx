"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api-types";

type MarketPanel = components["schemas"]["MarketPanel"];
type MarketPanelProps = {
  runId: string;
  mode: "new_idea" | "alternative" | "new_feature";
  incumbentName?: string;
  incumbentId?: string;
};

function ConfidenceBadge({ confidence }: { confidence: "high" | "medium" | "low" | "none" }) {
  const labels = {
    high: "High",
    medium: "Medium",
    low: "Low",
    none: "No signal",
  };
  const colors = {
    high: "var(--good)",
    medium: "var(--warn)",
    low: "var(--accent)",
    none: "var(--muted)",
  };
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: "0.75rem",
        fontWeight: 600,
        padding: "1px 8px",
        borderRadius: "999px",
        backgroundColor: colors[confidence],
        color: confidence === "high" || confidence === "none" ? "var(--surface)" : "var(--text)",
        border: "1px solid var(--line)",
      }}
    >
      {labels[confidence]}
    </span>
  );
}

function AvailabilityNotice({ availability }: { availability: string | null | undefined }) {
  if (!availability) return null;
  const messages: Record<string, string> = {
    not_collected: "This data has not been collected yet. Run pricing/traction/changelog collection to populate it.",
    no_history: "No historical data available for this signal.",
    insufficient_data: "Insufficient data to compute this signal (too few reviews or observations).",
    skipped: "Collection was skipped (e.g., robots.txt disallows, fetch failed, or quota exhausted).",
  };
  return (
    <p className="muted" style={{ fontStyle: "italic" }}>
      {messages[availability] ?? availability}
    </p>
  );
}

function PricingSection({ pricing, productNames }: { pricing: MarketPanel["pricing"]; productNames: Record<string, string> }) {
  if (!pricing.snapshots.length && !pricing.alerts.length) {
    return (
      <section aria-label="Pricing" className="market-section">
        <h3>Pricing & Price History</h3>
        <AvailabilityNotice availability={pricing.availability} />
      </section>
    );
  }

  return (
    <section aria-label="Pricing" className="market-section">
      <h3>Pricing & Price History</h3>
      <AvailabilityNotice availability={pricing.availability} />

      {pricing.snapshots.length > 0 && (
        <div>
          <h4>Latest Snapshots</h4>
          <ul className="cards">
            {pricing.snapshots.map((snapshot) => (
              <li key={`${snapshot.product_id}-${snapshot.url}`} className="card">
                <p className="facts">
                  <strong>{productNames[snapshot.product_id] ?? snapshot.product_id}</strong>{" "}
                  — fetched {new Date(snapshot.fetched_at).toLocaleString()}
                </p>
                <ul>
                  {snapshot.plans.map((plan) => (
                    <li key={plan.name}>
                      <strong>{plan.name}:</strong>{" "}
                      {plan.is_free
                        ? "Free"
                        : plan.contact_sales
                        ? "Contact sales"
                        : plan.prices
                            .map(
                              (p) =>
                                `${p.currency} ${p.amount} per ${p.per_seat ? "seat " : ""}${p.period}${p.billed_annually ? " (billed annually)" : ""}`
                            )
                            .join(", ")}
                      {plan.limits.length > 0 && (
                        <ul style={{ margin: "4px 0 0 16px" }}>
                          {plan.limits.map((limit, i) => (
                            <li key={i} className="muted" style={{ fontSize: "0.88rem" }}>
                              Limit: {limit}
                            </li>
                          ))}
                        </ul>
                      )}
                      {plan.features.length > 0 && (
                        <ul style={{ margin: "4px 0 0 16px" }}>
                          {plan.features.map((feature, i) => (
                            <li key={i} className="muted" style={{ fontSize: "0.88rem" }}>
                              {feature}
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
                <p className="muted" style={{ fontSize: "0.88rem" }}>
                  Free tier: {snapshot.free_tier ? "Yes" : "No"}{" "}
                  | Free trial: {snapshot.free_trial ? (snapshot.trial_days ? `${snapshot.trial_days} days` : "Yes") : "No"}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {pricing.alerts.length > 0 && (
        <div>
          <h4>Price Change Alerts</h4>
          <ul className="cards">
            {pricing.alerts.map((alert) => (
              <li key={`${alert.product_id}-${alert.detected_at}`} className="card">
                <p className="facts">
                  <strong>{productNames[alert.product_id] ?? alert.product_name}</strong>{" "}
                  — changed between {new Date(alert.previous_fetched_at).toLocaleDateString()} and{" "}
                  {new Date(alert.detected_at).toLocaleDateString()}
                </p>
                <ul>
                  {alert.changes.map((change, i) => (
                    <li key={i} className="muted" style={{ fontSize: "0.9rem" }}>
                      {change.kind === "price_increased" && (
                        <>
                          <span style={{ color: "var(--bad)" }}>↑ Price increased:</span>{" "}
                          {change.plan}: {change.currency} {change.old_amount} →{" "}
                          {change.new_amount} per {change.period}
                          {change.billed_annually ? " (billed annually)" : ""}
                        </>
                      )}
                      {change.kind === "price_decreased" && (
                        <>
                          <span style={{ color: "var(--good)" }}>↓ Price decreased:</span>{" "}
                          {change.plan}: {change.currency} {change.old_amount} →{" "}
                          {change.new_amount} per {change.period}
                          {change.billed_annually ? " (billed annually)" : ""}
                        </>
                      )}
                      {change.kind === "plan_added" && <span>Plan added: {change.plan}</span>}
                      {change.kind === "plan_removed" && <span>Plan removed: {change.plan}</span>}
                      {change.kind === "price_added" && (
                        <span>New price option: {change.plan} — {change.currency} {change.new_amount} per {change.period}</span>
                      )}
                      {change.kind === "price_removed" && (
                        <span>Price option removed: {change.plan} — {change.currency} {change.old_amount} per {change.period}</span>
                      )}
                      {change.kind === "limits_changed" && (
                        <span>
                          Limits changed: {change.plan} — from [{change.old_limits.join("; ")}] to [{change.new_limits.join("; ")}]
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function TractionSection({ traction }: { traction: MarketPanel["traction"] }) {
  if (!traction.scores.length) {
    return (
      <section aria-label="Traction" className="market-section">
        <h3>Traction</h3>
        <AvailabilityNotice availability={traction.availability} />
      </section>
    );
  }

  return (
    <section aria-label="Traction" className="market-section">
      <h3>Traction</h3>
      <ul className="cards">
        {traction.scores.map((score) => (
          <li key={score.product_id} className="card">
            <p className="facts">
              <strong>{score.label ?? "Not enough data"}</strong>{" "}
              {score.confidence && (
                <ConfidenceBadge confidence={score.confidence} />
              )}
            </p>
            <p className="muted">{score.note}</p>
            <ul style={{ marginTop: "8px" }}>
              {score.readings.map((reading) => (
                <li key={reading.signal} className="muted" style={{ fontSize: "0.9rem" }}>
                  {reading.signal}: {reading.direction} — {reading.basis}
                </li>
              ))}
            </ul>
            {score.missing.length > 0 && (
              <ul style={{ marginTop: "8px" }}>
                {score.missing.map((m) => (
                  <li key={m.signal} className="muted" style={{ fontSize: "0.88rem", fontStyle: "italic" }}>
                    {m.signal}: missing — {m.reason}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SwitchingSection({ switching }: { switching: MarketPanel["switching"] }) {
  if (!switching.rows.length) {
    return (
      <section aria-label="Switching" className="market-section">
        <h3>Switching Intent</h3>
        <AvailabilityNotice availability={switching.availability} />
      </section>
    );
  }

  return (
    <section aria-label="Switching" className="market-section">
      <h3>Switching Intent</h3>
      <p className="facts">{switching.total_reviews} switching-related reviews</p>
      <ul className="cards">
        {switching.rows.map((row) => (
          <li key={`${row.from_product}-${row.to_product}-${row.count}`} className="card">
            <p>
              {row.from_product ? <strong>{row.from_product}</strong> : <em>Unknown source</em>}{" "}
              →{" "}
              {row.to_product ? <strong>{row.to_product}</strong> : <em>Unknown destination</em>}
            </p>
            <p className="facts">{row.count} review{row.count !== 1 ? "s" : ""}</p>
            {row.reasons.length > 0 && (
              <ul>
                {row.reasons.map((reason, i) => (
                  <li key={i} className="muted" style={{ fontSize: "0.9rem" }}>
                    {reason}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ChangelogSection({ changelog }: { changelog: MarketPanel["changelog"] }) {
  if (!changelog.alerts.length) {
    return (
      <section aria-label="Changelog" className="market-section">
        <h3>Changelog Alerts</h3>
        <AvailabilityNotice availability={changelog.availability} />
        {changelog.items_matched > 0 && (
          <p className="muted">{changelog.items_matched} release items compared, no alerts raised.</p>
        )}
      </section>
    );
  }

  return (
    <section aria-label="Changelog" className="market-section">
      <h3>Changelog Alerts</h3>
      <p className="facts">{changelog.items_matched} release items compared</p>
      <ul className="cards">
        {changelog.alerts.map((alert) => (
          <li key={alert.item.id} className="card">
            {alert.kind === "shipped_fix" ? (
              <>
                <p>
                  <span style={{ color: "var(--good)", fontWeight: 600 }}>SHIPPED FIX</span>{" "}
                  {alert.product_name} shipped a fix for:{ " "}
                  <strong>{alert.pain_point_labels.join(", ")}</strong>
                </p>
                <p className="facts">
                  {alert.item.title} ({alert.item.version ?? "undated"}, {alert.item.source})
                  {alert.item.url && <a href={alert.item.url} target="_blank" rel="noopener noreferrer"> →</a>}
                </p>
                <p className="muted">Why: {alert.reason}</p>
                {Object.entries(alert.follow_ups).length > 0 && (
                  <details>
                    <summary>Trend follow-up</summary>
                    <ul>
                      {Object.entries(alert.follow_ups).map(([cluster, follow]) => (
                        <li key={cluster} className="muted" style={{ fontSize: "0.9rem" }}>
                          {cluster}: {follow.share_before != null ? `${Math.round(follow.share_before * 100)}%` : "too few"} before →{" "}
                          {follow.share_after != null ? `${Math.round(follow.share_after * 100)}%` : "too few"} after{" "}
                          {follow.change != null ? (
                            <span style={{ color: follow.change < 0 ? "var(--good)" : "var(--bad)" }}>
                              ({" "}{follow.change > 0 ? "+" : ""}{Math.round(follow.change * 100)}%)
                            </span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </>
            ) : (
              <>
                <p>
                  <span style={{ color: "var(--accent)", fontWeight: 600 }}>UNPLANNED FEATURE</span>{" "}
                  {alert.product_name} shipped something not in our roadmap
                </p>
                <p className="facts">
                  {alert.item.title} ({alert.item.version ?? "undated"}, {alert.item.source})
                  {alert.item.url && <a href={alert.item.url} target="_blank" rel="noopener noreferrer"> →</a>}
                </p>
                <p className="muted">Why: {alert.reason}</p>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function CombinedSignalsSection({ signals }: { signals: MarketPanel["combined_signals"] }) {
  if (!signals.length) {
    return (
      <section aria-label="Combined Signals" className="market-section">
        <h3>Combined Price/Complaint Signals</h3>
        <p className="muted">No price increases followed by complaint share rises detected.</p>
      </section>
    );
  }

  return (
    <section aria-label="Combined Signals" className="market-section">
      <h3>Combined Price/Complaint Signals</h3>
      <p className="muted">
        A price increase followed by a rise in the &#34;too expensive&#34; complaint share. Association only, not causation.
      </p>
      <ul className="cards">
        {signals.map((signal) => (
          <li key={`${signal.product_id}-${signal.price_increase_date}`} className="card">
            <p className="facts">
              <strong>{signal.product_name}</strong> — price change detected{" "}
              {new Date(signal.price_increase_date).toLocaleDateString()}
            </p>
            <p className="muted">{signal.note}</p>
            <div style={{ marginTop: "8px" }}>
              <p className="muted" style={{ fontSize: "0.88rem" }}>
                Pricing complaint: <strong>{signal.pain_point_label}</strong> (cluster {signal.cluster_id})
              </p>
              {signal.share_before != null && signal.share_after != null ? (
                <p className="muted" style={{ fontSize: "0.88rem" }}>
                  Share before: <strong>{Math.round(signal.share_before * 100)}%</strong> → after:{" "}
                  <strong>{Math.round(signal.share_after * 100)}%</strong>{" "}
                  {signal.share_change != null ? (
                    <>
                      ({" "}{signal.share_change > 0 ? "+" : ""}{Math.round(signal.share_change * 100)}%)
                    </>
                  ) : null}
                </p>
              ) : null}
              <ConfidenceBadge confidence={signal.confidence} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function MarketGapsSection({ gaps }: { gaps: MarketPanel["market_gaps"] }) {
  if (!gaps.length) {
    return (
      <section aria-label="Market Gaps" className="market-section">
        <h3>Market Gaps</h3>
        <p className="muted">No market gaps identified.</p>
      </section>
    );
  }

  return (
    <section aria-label="Market Gaps" className="market-section">
      <h3>Market Gaps</h3>
      <p className="muted">Gaps the PRD can cite as evidence (each has a stable ID).</p>
      <ul className="cards">
        {gaps.map((gap) => (
          <li key={gap.id} className="card">
            <p>
              <code style={{ fontSize: "0.8rem" }}>{gap.id}</code>{" "}
              <strong>{gap.product_name}</strong>
            </p>
            <p>{gap.fact}</p>
            <div style={{ marginTop: "8px", display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
              <ConfidenceBadge confidence={gap.confidence} />
              {gap.note && <span className="muted" style={{ fontSize: "0.85rem" }}>{gap.note}</span>}
              {gap.evidence_ids.length > 0 && (
                <details style={{ fontSize: "0.85rem" }}>
                  <summary className="muted">Evidence IDs ({gap.evidence_ids.length})</summary>
                  <ul style={{ marginTop: "4px" }}>
                    {gap.evidence_ids.map((eid, i) => (
                      <li key={i} className="muted" style={{ fontSize: "0.8rem" }}>
                        <code>{eid}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DifferentiationSection({
  differentiation,
  incumbentName,
}: {
  differentiation: MarketPanel["differentiation"];
  incumbentName?: string;
}) {
  if (!differentiation.length) {
    return (
      <section aria-label="Differentiation" className="market-section">
        <h3>Differentiation Opportunities (Mode 2)</h3>
        <AvailabilityNotice
          availability={
            incumbentName ? "no_history" : "not_collected"
          }
        />
      </section>
    );
  }

  return (
    <section aria-label="Differentiation" className="market-section">
      <h3>Differentiation Opportunities vs. {incumbentName ?? "Incumbent"}</h3>
      <p className="muted">
        Where the incumbent is weak, backed by pain points and market gaps.
      </p>
      <ul className="cards">
        {differentiation.map((opp, i) => (
          <li key={`${opp.area}-${i}`} className="card">
            <p>
              <strong>{opp.area}</strong>
            </p>
            <p>{opp.description}</p>
            <div style={{ marginTop: "8px", display: "flex", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
              <ConfidenceBadge confidence={opp.confidence} />
              {opp.evidence_ids.length > 0 && (
                <details style={{ fontSize: "0.85rem" }}>
                  <summary className="muted">Evidence IDs ({opp.evidence_ids.length})</summary>
                  <ul style={{ marginTop: "4px" }}>
                    {opp.evidence_ids.map((eid, j) => (
                      <li key={j} className="muted" style={{ fontSize: "0.8rem" }}>
                        <code>{eid}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function MarketPanelView({ runId, mode, incumbentName, incumbentId }: MarketPanelProps) {
  const [panel, setPanel] = useState<MarketPanel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api
      .getMarketPanel(runId)
      .then((data) => {
        if (!cancelled) setPanel(data as MarketPanel);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  if (loading) {
    return <p className="muted">Loading market panel…</p>;
  }
  if (error) {
    return <p className="banner failed">Failed to load market panel: {error}</p>;
  }
  if (!panel) {
    return <p className="muted">No market panel data.</p>;
  }

  const productNames: Record<string, string> = {};
  panel.pricing.snapshots.forEach((s) => {
    productNames[s.product_id] = s.plans[0]?.name ?? s.product_id;
  });
  panel.pricing.alerts.forEach((a) => {
    productNames[a.product_id] = a.product_name;
  });
  panel.traction.scores.forEach((s) => {
    productNames[s.product_id] = s.product_id;
  });

  return (
    <section aria-label="Market Opportunity Panel" className="market-panel">
      <h2>Market Opportunity Panel</h2>
      <p className="muted">
        Combined pricing, traction, switching, changelog, and gaps for this run.
        {panel.mode === "alternative" && incumbentName && (
          <> Mode 2: alternative to <strong>{incumbentName}</strong>.</>
        )}
      </p>

      <PricingSection pricing={panel.pricing} productNames={productNames} />
      <TractionSection traction={panel.traction} />
      <SwitchingSection switching={panel.switching} />
      <ChangelogSection changelog={panel.changelog} />
      <CombinedSignalsSection signals={panel.combined_signals} />
      <MarketGapsSection gaps={panel.market_gaps} />
      <DifferentiationSection
        differentiation={panel.differentiation}
        incumbentName={incumbentName}
      />
    </section>
  );
}