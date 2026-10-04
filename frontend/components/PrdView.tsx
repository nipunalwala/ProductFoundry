import type { PrdExport } from "@/lib/exports";

import { QuoteBlock } from "./QuoteBlock";

/** The PRD. Every requirement shows the evidence it cites and links to it. */
export function PrdView({ prd }: { prd: PrdExport }) {
  const evidence = Object.fromEntries(prd.evidence.map((item) => [item.id, item]));
  return (
    <section aria-label="PRD">
      <h2>PRD</h2>
      <h3>Problem</h3>
      <p>{prd.problem}</p>
      <h3>Users</h3>
      <p>{prd.users}</p>
      <h3>Goals</h3>
      <ul>
        {prd.goals.map((goal) => (
          <li key={goal}>{goal}</li>
        ))}
      </ul>
      {prd.non_goals.length > 0 ? (
        <>
          <h3>Non-goals</h3>
          <ul>
            {prd.non_goals.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>Requirements</h3>
      <ol className="cards">
        {prd.requirements.map((requirement) => (
          <li key={requirement.id} className="card">
            <p>
              <span className="tag">{requirement.priority}</span> {requirement.statement}
            </p>
            <p className="muted">Evidence:</p>
            <ul>
              {requirement.evidence.map((id) => {
                const item = evidence[id];
                if (!item) return <li key={id}>{id}</li>;
                return item.kind === "pain_point" ? (
                  <li key={id}>
                    <a href={`#evidence-${id}`}>{item.label}</a>: {item.review_count} reviews,{" "}
                    {Math.round(item.negative_share * 100)}% negative, severity {item.severity}/5
                  </li>
                ) : (
                  <li key={id}>
                    <a href={`#evidence-${id}`}>Market gap: {item.product}</a>
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>

      <h3>Success metrics</h3>
      <ul>
        {prd.success_metrics.map((metric) => (
          <li key={metric}>{metric}</li>
        ))}
      </ul>

      <h3>Evidence</h3>
      <ul className="cards">
        {prd.evidence.map((item) => (
          <li key={item.id} id={`evidence-${item.id}`} className="card">
            {item.kind === "pain_point" ? (
              <>
                <h4>{item.label}</h4>
                <p>{item.description}</p>
                <p className="facts">
                  {item.review_count} reviews · {Math.round(item.negative_share * 100)}% negative ·
                  severity {item.severity}/5
                </p>
                <QuoteBlock quote={item.quote} />
              </>
            ) : (
              <>
                <h4>Market gap: {item.product}</h4>
                <p>{item.fact}</p>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
