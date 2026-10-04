import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PainPointCheckpoint } from "@/components/PainPointCheckpoint";
import { TrendChart } from "@/components/TrendChart";
import { trendSentence } from "@/lib/exports";

import { REPORT } from "./fixtures";

describe("the trend chart", () => {
  it("draws a line per top pain point, with a legend and a table of the same numbers", () => {
    const { container } = render(<TrendChart points={REPORT.pain_points} />);
    const legend = container.querySelector(".legend")!;
    expect(within(legend as HTMLElement).getAllByRole("listitem")).toHaveLength(3);
    expect(legend).toHaveTextContent("1. Payments fail after money is debited");
    expect(legend).toHaveTextContent("rising");
    expect(screen.getByRole("img")).toHaveAccessibleName(/monthly share of reviews/);

    // The same numbers are available without reading colour.
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows).toHaveLength(7);
    expect(rows[1]).toHaveTextContent("2026-01");
    expect(rows[1]).toHaveTextContent("4 (10%)");
    expect(rows[4]).toHaveTextContent("8 (20%)");
  });

  it("leaves a gap for a month with too few reviews instead of drawing zero", () => {
    const { container } = render(<TrendChart points={REPORT.pain_points} />);
    const first = container.querySelector("g.series-1")!;
    // Six months, one of them too thin: five markers, and the line is broken in two.
    expect(first.querySelectorAll("circle")).toHaveLength(5);
    expect(first.querySelectorAll("path")).toHaveLength(2);
    // Each line carries its pain point's number at its end, so colour is not the only key.
    expect(first.querySelector("text")).toHaveTextContent("1");
    const march = within(screen.getByRole("table")).getAllByRole("row")[3];
    expect(march).toHaveTextContent("0 (too few to say)");
  });

  it("reads out a month's numbers when it is pointed at", async () => {
    const user = userEvent.setup();
    render(<TrendChart points={REPORT.pain_points} />);
    expect(screen.getByRole("status")).toHaveTextContent("Point at a month");
    await user.hover(screen.getByLabelText("Show 2026-05"));
    expect(screen.getByRole("status")).toHaveTextContent("2026-05: 1. 20% (8 of 40)");
    await user.hover(screen.getByLabelText("Show 2026-03"));
    expect(screen.getByRole("status")).toHaveTextContent("1. too few reviews (0 of 2)");
  });

  it("says so when there is nothing to draw", () => {
    const thin = REPORT.pain_points.map((point) => ({
      ...point,
      trend: {
        ...point.trend,
        months: point.trend.months.map((m) => ({ ...m, share: null, enough: false })),
      },
    }));
    render(<TrendChart points={thin} />);
    expect(screen.getByText(/Not enough reviews per month/)).toBeInTheDocument();
  });
});

describe("trends and switching on a pain point", () => {
  it("says where each pain point is heading", () => {
    render(<PainPointCheckpoint report={REPORT} open={false} onApprove={vi.fn()} />);
    const first = document.querySelector<HTMLElement>("ol.cards > li")!;
    expect(first).toHaveTextContent(
      "20% of all reviews in the last 3 months, 10% in the 3 before (+100%).",
    );
    const none = { ...REPORT.pain_points[0].trend, recent_share: null, growth: null };
    expect(trendSentence(none, 3)).toMatch(/Not enough reviews/);
  });

  it("filters a pain point down to its reviews about switching", async () => {
    const user = userEvent.setup();
    render(<PainPointCheckpoint report={REPORT} open={false} onApprove={vi.fn()} />);
    expect(screen.queryByText(/switching to Tabby/)).toBeNull();

    const filter = screen.getByLabelText("Only reviews about switching, for pain point 1 (1)");
    await user.click(filter);
    expect(screen.getByText("payment failed again so I am switching to Tabby")).toBeVisible();
    expect(screen.getByText("Leaving")).toBeInTheDocument();
    expect(screen.getByText(/Other product: Tabby. Reason: payments keep failing./)).toBeVisible();

    // A pain point with no such review cannot be filtered.
    expect(
      screen.getByLabelText("Only reviews about switching, for pain point 2 (0)"),
    ).toBeDisabled();
    await user.click(filter);
    expect(screen.queryByText(/switching to Tabby/)).toBeNull();
  });

  it("shows the switching table", () => {
    render(<PainPointCheckpoint report={REPORT} open={false} onApprove={vi.fn()} />);
    expect(screen.getByText(/3 reviews talk about switching products/)).toBeInTheDocument();
    const table = screen.getAllByRole("table").at(-1)!;
    const rows = within(table).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("SplitlyTabby2payments keep failing");
    expect(rows[2]).toHaveTextContent("Splitlynot said1none given");
  });
});
