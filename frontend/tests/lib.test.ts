import { describe, expect, it } from "vitest";

import { competitorApproval, NO_CHANGES, painPointApproval, painPointProblem } from "@/lib/edits";
import { EMPTY_FORM, formErrors, splitList, toRunInput } from "@/lib/form";

const FILLED = {
  ...EMPTY_FORM,
  idea: " A simpler bill-splitting app ",
  targetUsers: "Flatmates in India",
  incumbentName: "Splitwise",
  incumbentUrl: "https://www.splitwise.com",
  googlePlayId: "com.Splitwise.SplitwiseMobile",
  appStoreId: "458023433",
  knownCompetitors: "Tricount\nSettle Up, ",
  techStack: "Flutter, FastAPI",
  region: "in",
};

describe("the input form", () => {
  it("becomes a RunInput the backend accepts", () => {
    expect(formErrors(FILLED)).toEqual({});
    expect(toRunInput(FILLED)).toEqual({
      schema_version: 1,
      mode: "alternative",
      idea: "A simpler bill-splitting app",
      target_users: "Flatmates in India",
      platforms: ["android", "ios"],
      region: "IN",
      incumbent: {
        name: "Splitwise",
        urls: ["https://www.splitwise.com"],
        store_ids: { google_play: "com.Splitwise.SplitwiseMobile", app_store: "458023433" },
      },
      known_competitors: ["Tricount", "Settle Up"],
      tech_stack: ["Flutter", "FastAPI"],
    });
  });

  it("leaves out what was not given", () => {
    const input = toRunInput({ ...FILLED, incumbentUrl: "", googlePlayId: "", appStoreId: "" });
    expect(input.incumbent).toEqual({
      name: "Splitwise",
      urls: [],
      store_ids: { google_play: null, app_store: null },
    });
    expect(toRunInput({ ...FILLED, mode: "new_idea", incumbentName: "" }).incumbent).toBeNull();
  });

  it("says what is missing before anything is sent", () => {
    expect(Object.keys(formErrors(EMPTY_FORM)).sort()).toEqual([
      "idea",
      "incumbentName",
      "targetUsers",
    ]);
    const wrong = formErrors({
      ...FILLED,
      platforms: [],
      region: "India",
      incumbentUrl: "splitwise.com",
      googlePlayId: "splitwise",
      appStoreId: "id458",
    });
    expect(Object.keys(wrong).sort()).toEqual([
      "appStoreId",
      "googlePlayId",
      "incumbentUrl",
      "platforms",
      "region",
    ]);
    // Only an alternative needs an incumbent.
    expect(formErrors({ ...FILLED, mode: "new_idea", incumbentName: "" })).toEqual({});
  });

  it("splits lists on commas and new lines", () => {
    expect(splitList(" a, b\n\nc ,")).toEqual(["a", "b", "c"]);
    expect(splitList("")).toEqual([]);
  });
});

describe("checkpoint edits", () => {
  it("approve unchanged when nothing was edited", () => {
    expect(competitorApproval([], [])).toBeUndefined();
    expect(painPointApproval(NO_CHANGES, { cl_a: "Payments fail" })).toBeUndefined();
    expect(
      painPointApproval({ ...NO_CHANGES, renames: { cl_a: " Payments fail " } }, {
        cl_a: "Payments fail",
      }),
    ).toBeUndefined();
  });

  it("send the removed and added competitors", () => {
    const added = {
      name: "Tricount",
      url: "https://tricount.com",
      positioning: "Splits trip costs",
      target_users: "Travellers",
    };
    expect(competitorApproval(["prod_b"], [added])).toEqual({
      competitors: { remove: ["prod_b"], add: [added] },
    });
  });

  it("send renames, drops and merges by cluster id, the merge target first", () => {
    const labels = { cl_a: "Payments fail", cl_b: "Paywall", cl_c: "Limit", cl_d: "Ads" };
    const body = painPointApproval(
      {
        renames: { cl_a: "Money is lost", cl_c: "ignored: it is merged away" },
        dropped: ["cl_d"],
        mergeInto: { cl_c: "cl_b" },
      },
      labels,
    );
    expect(body).toEqual({
      pain_points: {
        rename: { cl_a: "Money is lost" },
        merge: [["cl_b", "cl_c"]],
        drop: ["cl_d"],
        rank: [],
      },
    });
  });

  it("refuse a pain point that is both dropped and merged, or a chain of merges", () => {
    expect(painPointProblem(NO_CHANGES)).toBeNull();
    expect(
      painPointProblem({ renames: {}, dropped: ["cl_a"], mergeInto: { cl_a: "cl_b" } }),
    ).toMatch(/both dropped and merged/);
    expect(
      painPointProblem({ renames: {}, dropped: [], mergeInto: { cl_a: "cl_b", cl_b: "cl_c" } }),
    ).toMatch(/not itself being merged/);
  });
});
