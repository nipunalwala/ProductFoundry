"use client";

import { useState } from "react";

import type { ExportedPainPoint } from "@/lib/exports";

const WIDTH = 720;
const HEIGHT = 260;
const PAD = { top: 16, right: 28, bottom: 28, left: 40 };
const MAX_SERIES = 3; // the top pain points; more lines than this stop being readable

function percent(share: number): string {
  return `${Math.round(share * 100)}%`;
}

/** Share of all reviews per month for the top pain points. A month with too few reviews
 * has no point and breaks the line: a gap, not a zero. */
export function TrendChart({ points }: { points: ExportedPainPoint[] }) {
  const [active, setActive] = useState<number | null>(null);
  const series = points.slice(0, MAX_SERIES);
  const months = series[0]?.trend.months.map((month) => month.month) ?? [];
  const shares = series.flatMap((point) =>
    point.trend.months.flatMap((month) => (month.share === null ? [] : [month.share])),
  );
  if (months.length < 2 || shares.length === 0) {
    return <p className="muted">Not enough reviews per month to draw a trend.</p>;
  }

  // The axis ends on the next multiple of 10% above the largest share.
  const top = Math.max(0.1, Math.ceil(Math.max(...shares) * 10) / 10);
  const plotWidth = WIDTH - PAD.left - PAD.right;
  const plotHeight = HEIGHT - PAD.top - PAD.bottom;
  const x = (index: number) => PAD.left + (plotWidth * index) / (months.length - 1);
  const y = (share: number) => PAD.top + plotHeight * (1 - share / top);
  const ticks = [0, top / 2, top];

  // One path per run of consecutive months that have a share.
  const paths = (point: ExportedPainPoint) => {
    const runs: string[] = [];
    let current = "";
    point.trend.months.forEach((month, index) => {
      if (month.share === null) {
        if (current) runs.push(current);
        current = "";
      } else {
        current += `${current ? "L" : "M"}${x(index).toFixed(1)},${y(month.share).toFixed(1)}`;
      }
    });
    if (current) runs.push(current);
    return runs;
  };

  // Where a line ends: its number is written there, matching the legend.
  const lastPoint = (point: ExportedPainPoint) => {
    for (let index = point.trend.months.length - 1; index >= 0; index--) {
      const share = point.trend.months[index].share;
      if (share !== null) return { x: x(index), y: y(share) };
    }
    return null;
  };

  const band = plotWidth / (months.length - 1);
  return (
    <figure className="chart">
      <figcaption>
        Share of all reviews each month, for the top {series.length} pain points. A gap means too
        few reviews that month to say.
      </figcaption>
      <ul className="legend">
        {series.map((point, index) => (
          <li key={point.cluster_id}>
            <span className={`swatch series-${index + 1}`} aria-hidden="true" />
            {point.rank}. {point.label}
            {point.trend.rising ? <span className="tag">rising</span> : null}
          </li>
        ))}
      </ul>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label="Line chart of each top pain point's monthly share of reviews. The same numbers are in the table below."
        onMouseLeave={() => setActive(null)}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="grid" x1={PAD.left} x2={WIDTH - PAD.right} y1={y(tick)} y2={y(tick)} />
            <text className="axis" x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
              {percent(tick)}
            </text>
          </g>
        ))}
        {months.map((month, index) =>
          index % 2 === (months.length - 1) % 2 ? (
            <text key={month} className="axis" x={x(index)} y={HEIGHT - 8} textAnchor="middle">
              {month}
            </text>
          ) : null,
        )}
        {active !== null ? (
          <line className="crosshair" x1={x(active)} x2={x(active)} y1={PAD.top} y2={y(0)} />
        ) : null}
        {series.map((point, index) => (
          <g key={point.cluster_id} className={`series-${index + 1}`}>
            {paths(point).map((d) => (
              <path key={d} className="line" d={d} />
            ))}
            {point.trend.months.map((month, at) =>
              month.share === null ? null : (
                <circle key={month.month} className="marker" cx={x(at)} cy={y(month.share)} r={4} />
              ),
            )}
            {lastPoint(point) ? (
              <text className="end-label" x={lastPoint(point)!.x + 9} y={lastPoint(point)!.y + 4}>
                {point.rank}
              </text>
            ) : null}
          </g>
        ))}
        {months.map((month, index) => (
          <rect
            key={month}
            className="hit"
            x={x(index) - band / 2}
            y={PAD.top}
            width={band}
            height={plotHeight}
            tabIndex={0}
            aria-label={`Show ${month}`}
            onMouseEnter={() => setActive(index)}
            onFocus={() => setActive(index)}
            onBlur={() => setActive(null)}
          />
        ))}
      </svg>
      <p className="tooltip" role="status">
        {active === null
          ? "Point at a month to read its numbers."
          : `${months[active]}: ` +
            series
              .map((point) => {
                const month = point.trend.months[active];
                const share = month.share === null ? "too few reviews" : percent(month.share);
                return `${point.rank}. ${share} (${month.reviews} of ${month.total_reviews})`;
              })
              .join(" · ")}
      </p>
      <details>
        <summary>The numbers as a table</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Month</th>
                <th scope="col">All reviews</th>
                {series.map((point) => (
                  <th scope="col" key={point.cluster_id}>
                    {point.rank}. {point.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {months.map((month, index) => (
                <tr key={month}>
                  <th scope="row">{month}</th>
                  <td>{series[0].trend.months[index].total_reviews}</td>
                  {series.map((point) => {
                    const cell = point.trend.months[index];
                    return (
                      <td key={point.cluster_id}>
                        {cell.reviews}
                        {cell.share === null ? " (too few to say)" : ` (${percent(cell.share)})`}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
