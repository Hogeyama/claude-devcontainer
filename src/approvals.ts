// Requests waiting for a person, one decision each.

import { randomBytes } from "node:crypto";
import type { ExecRequest } from "./request.ts";

export const HOLD_MS = 240_000;

export type Decision = "approved" | "denied" | "expired" | "gone";

export interface Held {
  id: string;
  exec: ExecRequest;
  since: number;
  expiresAt: number;
}

interface Entry extends Held {
  settle: (d: Decision) => void;
}

export class Approvals {
  private readonly entries = new Map<string, Entry>();
  private readonly listeners = new Set<(held: Held) => void>();

  constructor(private readonly holdMs = HOLD_MS) {}

  /**
   * Hold a request until someone decides it, it expires, or `signal` (the
   * client going away) aborts it.
   */
  hold(exec: ExecRequest, signal?: AbortSignal): { held: Held; decision: Promise<Decision> } {
    const id = randomBytes(16).toString("hex");
    const since = Date.now();
    let settle!: (d: Decision) => void;
    const decision = new Promise<Decision>((resolve) => {
      settle = resolve;
    });
    const entry: Entry = {
      id,
      exec,
      since,
      expiresAt: since + this.holdMs,
      settle: () => {},
    };
    const timer = setTimeout(() => finish("expired"), this.holdMs);
    const onAbort = () => finish("gone");
    const finish = (d: Decision) => {
      if (!this.entries.delete(id)) return;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      settle(d);
    };
    entry.settle = finish;
    this.entries.set(id, entry);
    if (signal?.aborted) finish("gone");
    else signal?.addEventListener("abort", onAbort, { once: true });
    const held = view(entry);
    for (const l of this.listeners) l(held);
    return { held, decision };
  }

  list(): Held[] {
    return [...this.entries.values()].map(view);
  }

  /** Whether a request with that id was waiting and is now decided. */
  decide(id: string, approve: boolean): boolean {
    const entry = this.entries.get(id);
    if (!entry) return false;
    entry.settle(approve ? "approved" : "denied");
    return true;
  }

  onHold(listener: (held: Held) => void): void {
    this.listeners.add(listener);
  }

  /** Deny everything still waiting, as the server stops. */
  denyAll(): void {
    for (const entry of [...this.entries.values()]) entry.settle("denied");
  }
}

const view = (e: Entry): Held => ({
  id: e.id,
  exec: e.exec,
  since: e.since,
  expiresAt: e.expiresAt,
});
