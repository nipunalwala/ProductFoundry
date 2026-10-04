"use client";

import { useRouter } from "next/navigation";

import { RunForm } from "@/components/RunForm";
import { api } from "@/lib/api";
import type { RunInput } from "@/lib/api";

export default function NewRunPage() {
  const router = useRouter();

  async function start(input: RunInput) {
    const run = await api.createRun(input);
    router.push(`/runs/${run.id}`);
  }

  return (
    <>
      <h1>New run</h1>
      <RunForm onSubmit={start} />
    </>
  );
}
