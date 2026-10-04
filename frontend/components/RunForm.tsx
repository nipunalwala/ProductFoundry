"use client";

import { useState } from "react";

import type { RunInput } from "@/lib/api";
import { EMPTY_FORM, formErrors, toRunInput } from "@/lib/form";
import type { FormState, Mode, Platform } from "@/lib/form";

const MODES: { value: Mode; label: string; ready: boolean }[] = [
  { value: "alternative", label: "An alternative to an existing product", ready: true },
  { value: "new_idea", label: "A new idea (not built yet)", ready: false },
  { value: "new_feature", label: "A new feature (not built yet)", ready: false },
];
const PLATFORMS: { value: Platform; label: string }[] = [
  { value: "android", label: "Android" },
  { value: "ios", label: "iOS" },
  { value: "web", label: "Web" },
];

export function RunForm({ onSubmit }: { onSubmit: (input: RunInput) => Promise<void> }) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [showErrors, setShowErrors] = useState(false);
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const errors = formErrors(form);

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function togglePlatform(platform: Platform) {
    const chosen = form.platforms.includes(platform)
      ? form.platforms.filter((item) => item !== platform)
      : [...form.platforms, platform];
    set("platforms", chosen);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setShowErrors(true);
    if (Object.keys(errors).length > 0) return;
    setSending(true);
    setFailure(null);
    try {
      await onSubmit(toRunInput(form));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : "The run could not be started.");
      setSending(false);
    }
  }

  const error = (key: keyof FormState) =>
    showErrors && errors[key] ? <p className="field-error">{errors[key]}</p> : null;

  return (
    <form onSubmit={submit} className="form" noValidate>
      <p className="notice" role="note">
        Privacy: your idea and review text are sent to third-party AI providers on their free
        plans. Google&apos;s Gemini free plan may use them to improve Google&apos;s products. Do
        not enter an idea you need to keep confidential.
      </p>

      <label>
        What are you building?
        <select value={form.mode} onChange={(e) => set("mode", e.target.value as Mode)}>
          {MODES.map((mode) => (
            <option key={mode.value} value={mode.value} disabled={!mode.ready}>
              {mode.label}
            </option>
          ))}
        </select>
      </label>

      <label>
        Product idea
        <textarea
          rows={3}
          value={form.idea}
          onChange={(e) => set("idea", e.target.value)}
          placeholder="A simpler bill-splitting app with no daily expense limit"
        />
        {error("idea")}
      </label>

      <label>
        Target users
        <input
          value={form.targetUsers}
          onChange={(e) => set("targetUsers", e.target.value)}
          placeholder="Flatmates and friends in India who share expenses"
        />
        {error("targetUsers")}
      </label>

      <fieldset>
        <legend>Platforms</legend>
        {PLATFORMS.map((platform) => (
          <label key={platform.value} className="inline">
            <input
              type="checkbox"
              checked={form.platforms.includes(platform.value)}
              onChange={() => togglePlatform(platform.value)}
            />
            {platform.label}
          </label>
        ))}
        {error("platforms")}
      </fieldset>

      <label>
        Region (two-letter country code)
        <input
          value={form.region}
          maxLength={2}
          className="short"
          onChange={(e) => set("region", e.target.value)}
        />
        {error("region")}
      </label>

      <fieldset>
        <legend>Existing product (the incumbent)</legend>
        <label>
          Name
          <input
            value={form.incumbentName}
            onChange={(e) => set("incumbentName", e.target.value)}
            placeholder="Splitwise"
          />
          {error("incumbentName")}
        </label>
        <label>
          Website (optional)
          <input
            value={form.incumbentUrl}
            onChange={(e) => set("incumbentUrl", e.target.value)}
            placeholder="https://www.splitwise.com"
          />
          {error("incumbentUrl")}
        </label>
        <label>
          Google Play package (optional)
          <input
            value={form.googlePlayId}
            onChange={(e) => set("googlePlayId", e.target.value)}
            placeholder="com.example.app"
          />
          {error("googlePlayId")}
        </label>
        <label>
          App Store id (optional)
          <input
            value={form.appStoreId}
            onChange={(e) => set("appStoreId", e.target.value)}
            placeholder="458023433"
          />
          {error("appStoreId")}
        </label>
      </fieldset>

      <label>
        Competitors you already know (optional, one per line)
        <textarea
          rows={2}
          value={form.knownCompetitors}
          onChange={(e) => set("knownCompetitors", e.target.value)}
        />
      </label>

      <label>
        Tech stack (optional, comma separated)
        <input
          value={form.techStack}
          onChange={(e) => set("techStack", e.target.value)}
          placeholder="Flutter, FastAPI, PostgreSQL"
        />
      </label>

      {failure ? <p className="banner failed">{failure}</p> : null}
      <button type="submit" className="primary" disabled={sending}>
        {sending ? "Starting…" : "Start run"}
      </button>
    </form>
  );
}
