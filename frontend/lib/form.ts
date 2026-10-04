// The input form's state and how it becomes a RunInput. Pure, so it is tested without a browser.

import type { RunInput } from "./api";

export type Mode = RunInput["mode"];
export type Platform = RunInput["platforms"][number];

export type FormState = {
  mode: Mode;
  idea: string;
  targetUsers: string;
  platforms: Platform[];
  region: string;
  incumbentName: string;
  incumbentUrl: string;
  googlePlayId: string;
  appStoreId: string;
  knownCompetitors: string; // one per line or comma separated
  techStack: string;
};

export const EMPTY_FORM: FormState = {
  mode: "alternative",
  idea: "",
  targetUsers: "",
  platforms: ["android", "ios"],
  region: "IN",
  incumbentName: "",
  incumbentUrl: "",
  googlePlayId: "",
  appStoreId: "",
  knownCompetitors: "",
  techStack: "",
};

export function splitList(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** What stops the form from being sent, by field. Empty when it can be sent. */
export function formErrors(form: FormState): Partial<Record<keyof FormState, string>> {
  const errors: Partial<Record<keyof FormState, string>> = {};
  if (!form.idea.trim()) errors.idea = "Describe the product idea.";
  if (!form.targetUsers.trim()) errors.targetUsers = "Say who the product is for.";
  if (form.platforms.length === 0) errors.platforms = "Choose at least one platform.";
  if (!/^[A-Za-z]{2}$/.test(form.region.trim())) {
    errors.region = "Use a two-letter country code, for example IN.";
  }
  const hasIncumbent = Boolean(form.incumbentName.trim());
  if (form.mode === "alternative" && !hasIncumbent) {
    errors.incumbentName = "Name the product this is an alternative to.";
  }
  if (form.incumbentUrl.trim() && !/^https?:\/\/\S+$/.test(form.incumbentUrl.trim())) {
    errors.incumbentUrl = "Start the address with http:// or https://.";
  }
  if (form.googlePlayId.trim() && !/^[A-Za-z]\w*(\.[A-Za-z]\w*)+$/.test(form.googlePlayId.trim())) {
    errors.googlePlayId = "A package name looks like com.example.app.";
  }
  if (form.appStoreId.trim() && !/^[0-9]+$/.test(form.appStoreId.trim())) {
    errors.appStoreId = "An App Store id is a number.";
  }
  return errors;
}

export function toRunInput(form: FormState): RunInput {
  const name = form.incumbentName.trim();
  return {
    schema_version: 1,
    mode: form.mode,
    idea: form.idea.trim(),
    target_users: form.targetUsers.trim(),
    platforms: form.platforms,
    region: form.region.trim().toUpperCase(),
    incumbent: name
      ? {
          name,
          urls: form.incumbentUrl.trim() ? [form.incumbentUrl.trim()] : [],
          store_ids: {
            google_play: form.googlePlayId.trim() || null,
            app_store: form.appStoreId.trim() || null,
          },
        }
      : null,
    known_competitors: splitList(form.knownCompetitors),
    tech_stack: splitList(form.techStack),
  };
}
