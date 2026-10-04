import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CompetitorCheckpoint } from "@/components/CompetitorCheckpoint";
import { PainPointCheckpoint } from "@/components/PainPointCheckpoint";
import { PrdView } from "@/components/PrdView";
import { RunForm } from "@/components/RunForm";
import { StageProgress } from "@/components/StageProgress";
import { TaskList } from "@/components/TaskList";

import { COMPETITORS, PRD, REPORT, run, TASKS } from "./fixtures";

describe("the input form", () => {
  it("shows the privacy note and sends nothing until the form is complete", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<RunForm onSubmit={onSubmit} />);

    expect(screen.getByRole("note")).toHaveTextContent(/sent to third-party AI providers/);
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByText("Describe the product idea.")).toBeInTheDocument();
    expect(screen.getByText("Name the product this is an alternative to.")).toBeInTheDocument();

    // A field's error is part of its label, so it is read out with the field.
    await user.type(screen.getByLabelText(/^Product idea/), "A simpler bill splitter");
    await user.type(screen.getByLabelText(/^Target users/), "Flatmates");
    await user.type(screen.getByLabelText(/^Name/), "Splitwise");
    await user.click(screen.getByLabelText("iOS"));
    await user.click(screen.getByRole("button", { name: "Start run" }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    const input = onSubmit.mock.calls[0][0];
    expect(input.idea).toBe("A simpler bill splitter");
    expect(input.platforms).toEqual(["android"]);
    expect(input.incumbent.name).toBe("Splitwise");
    expect(input.region).toBe("IN");
  });

  it("shows why a run could not be started", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn().mockRejectedValue(new Error("the database is not running"));
    render(<RunForm onSubmit={onSubmit} />);
    await user.type(screen.getByLabelText("Product idea"), "x");
    await user.type(screen.getByLabelText("Target users"), "y");
    await user.type(screen.getByLabelText("Name"), "z");
    await user.click(screen.getByRole("button", { name: "Start run" }));
    expect(await screen.findByText("the database is not running")).toBeInTheDocument();
  });
});

describe("stage progress", () => {
  it("names every stage with its status", () => {
    render(<StageProgress run={run({}, ["completed", "running"])} />);
    const items = screen.getAllByRole("listitem");
    expect(items).toHaveLength(7);
    expect(items[0]).toHaveTextContent("Competitor research");
    expect(items[0]).toHaveTextContent("Done");
    expect(items[1]).toHaveTextContent("Running");
    expect(items[6]).toHaveTextContent("Acceptance criteria");
  });

  it("says a run out of quota is paused, not failed, and why", () => {
    const paused = run({ status: "paused_quota", pause_reason: "out of quota: groq, gemini" });
    render(<StageProgress run={paused} />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent("Paused, not failed.");
    expect(status).toHaveTextContent("out of quota: groq, gemini");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("says why a run failed and offers to try again", async () => {
    const user = userEvent.setup();
    const onResume = vi.fn();
    const failed = run({ status: "failed", error: "s2_reviews: the store answered 500" });
    render(<StageProgress run={failed} onResume={onResume} />);
    expect(screen.getByRole("alert")).toHaveTextContent("s2_reviews: the store answered 500");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onResume).toHaveBeenCalled();
  });
});

describe("the competitor checkpoint", () => {
  it("approves unchanged when nothing was edited", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<CompetitorCheckpoint output={COMPETITORS} open onApprove={onApprove} />);
    expect(screen.getByText("incumbent")).toBeInTheDocument();
    expect(screen.getByText(/A newsletter, not a product/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Approve competitors" }));
    expect(onApprove).toHaveBeenCalledWith(undefined);
  });

  it("sends what was removed and added", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<CompetitorCheckpoint output={COMPETITORS} open onApprove={onApprove} />);

    await user.click(screen.getByRole("button", { name: "Remove Tabby" }));
    await user.click(screen.getByRole("button", { name: "Add to the list" }));
    expect(screen.getByText(/needs a name and a website/)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Name"), "Tricount");
    await user.type(screen.getByLabelText("Website"), "https://tricount.example");
    await user.type(screen.getByLabelText("What it is"), "Splits trip costs");
    await user.type(screen.getByLabelText("Who it is for"), "Travellers");
    await user.click(screen.getByRole("button", { name: "Add to the list" }));
    expect(screen.getByText("added by you")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Approve competitors" }));
    expect(onApprove).toHaveBeenCalledWith({
      competitors: {
        remove: ["prod_b"],
        add: [
          {
            name: "Tricount",
            url: "https://tricount.example",
            positioning: "Splits trip costs",
            target_users: "Travellers",
          },
        ],
      },
    });
  });

  it("is read-only once approved, and shows a refused approval", async () => {
    const { unmount } = render(
      <CompetitorCheckpoint output={COMPETITORS} open={false} onApprove={vi.fn()} />,
    );
    expect(screen.queryByRole("button", { name: "Approve competitors" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove Tabby" })).toBeNull();
    unmount();

    const user = userEvent.setup();
    const onApprove = vi.fn().mockRejectedValue(new Error("edited CompetitorList is invalid"));
    render(<CompetitorCheckpoint output={COMPETITORS} open onApprove={onApprove} />);
    await user.click(screen.getByRole("button", { name: "Approve competitors" }));
    expect(await screen.findByText("edited CompetitorList is invalid")).toBeInTheDocument();
  });
});

describe("the pain-point checkpoint", () => {
  it("shows counts and quotes that open their source", () => {
    render(<PainPointCheckpoint report={REPORT} open={false} onApprove={vi.fn()} />);
    expect(screen.getByText(/44 reviews: 29 English, 14 Hinglish, 1 not analysed/)).toBeVisible();
    const first = screen.getAllByRole("listitem")[0];
    expect(first).toHaveTextContent("1. Payments fail after money is debited");
    expect(first).toHaveTextContent("Severity 4/5 · 19 reviews · 75% negative");
    expect(first).toHaveTextContent("payment fail ho gaya lekin paisa kat gaya");
    expect(first).toHaveTextContent("Translation: The payment failed but the money was deducted.");
    const source = within(first).getByRole("link", { name: "Google Play, 2026-08-01" });
    expect(source).toHaveAttribute("href", expect.stringContaining("reviewId=r1"));
    expect(source).toHaveAttribute("target", "_blank");
    expect(screen.getByText(/The reviews are vague/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve pain points" })).toBeNull();
  });

  it("sends renames, merges and drops", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn().mockResolvedValue(undefined);
    render(<PainPointCheckpoint report={REPORT} open onApprove={onApprove} />);

    const label = screen.getByLabelText("Label for pain point 2");
    await user.clear(label);
    await user.type(label, "Free plan limits expenses");
    await user.selectOptions(screen.getByLabelText("Merge pain point 3 into"), "cl_b");
    await user.click(screen.getByLabelText("Drop pain point 1"));
    await user.click(screen.getByRole("button", { name: "Approve pain points" }));

    expect(onApprove).toHaveBeenCalledWith({
      pain_points: {
        rename: { cl_b: "Free plan limits expenses" },
        merge: [["cl_b", "cl_c"]],
        drop: ["cl_a"],
        rank: [],
      },
    });
  });

  it("refuses a pain point that is both dropped and merged", async () => {
    const user = userEvent.setup();
    const onApprove = vi.fn();
    render(<PainPointCheckpoint report={REPORT} open onApprove={onApprove} />);
    await user.selectOptions(screen.getByLabelText("Merge pain point 3 into"), "cl_b");
    await user.click(screen.getByLabelText("Drop pain point 3"));
    await user.click(screen.getByRole("button", { name: "Approve pain points" }));
    expect(screen.getByText(/both dropped and merged/)).toBeInTheDocument();
    expect(onApprove).not.toHaveBeenCalled();
  });
});

describe("the PRD view", () => {
  it("links every citation to its evidence", () => {
    const { container } = render(<PrdView prd={PRD} />);
    expect(screen.getByText("The product must refund a failed payment automatically.")).toBeVisible();
    const citations = Array.from(container.querySelectorAll('a[href^="#evidence-"]'));
    expect(citations.map((a) => a.getAttribute("href"))).toEqual([
      "#evidence-cl_a",
      "#evidence-gap_b",
      "#evidence-cl_a",
    ]);
    for (const citation of citations) {
      const target = citation.getAttribute("href")!.slice(1);
      expect(container.querySelector(`[id="${target}"]`)).not.toBeNull();
    }
    expect(screen.getAllByText(/16 reviews/).length).toBeGreaterThan(0);
    expect(screen.getByText("Market gap: Tabby", { selector: "h4" })).toBeInTheDocument();
    expect(screen.getByText(/payment fail ho gaya/)).toBeInTheDocument();
  });
});

describe("the task list", () => {
  it("lists tasks in build order with their criteria", () => {
    render(<TaskList tasks={TASKS} />);
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["Track the state of every payment", "Refund failed payments"]);
    expect(screen.getByText(/effort L · after Track the state of every payment/)).toBeVisible();
    expect(screen.getByText("Happy path")).toBeInTheDocument();
    expect(screen.getByText("Failure")).toBeInTheDocument();
    expect(screen.getByText(/a refund is started/)).toBeInTheDocument();
    expect(screen.getAllByText("Acceptance criteria:")).toHaveLength(1);
  });
});
