// Badge (M6, design pass): the single source of truth for status colors
// (pending/running/succeeded/failed), previously hand-copied three times
// -- HistoryPage's STATUS_STYLES map, RunPanel's phase boxes, and
// RunPanel's ValidationResult success/failure boxes each picked their
// own emerald/amber/danger classes independently. STATUS_STYLES is
// exported so a caller that needs the *colors* but not the pill shape
// (RunPanel's full-width phase boxes are a different container, not a
// pill) can still pull from one shared definition instead of a fourth
// copy.
//
// The pulse dot is a motion.span, matching HistoryPage's original
// implementation exactly (opacity [1, 0.3, 1], 1.4s, easeInOut, infinite)
// -- kept on Framer Motion rather than switched to a CSS @keyframes, so
// this is a pure extraction with no animation behavior change.

import { motion } from "motion/react";

export const STATUS_STYLES = {
  pending: "border-amber-500/30 bg-amber-500/10 text-amber-500",
  running: "border-amber-500/30 bg-amber-500/10 text-amber-500",
  succeeded: "border-success-500/30 bg-success-500/10 text-success-400",
  failed: "border-danger/30 bg-danger/10 text-danger",
};

// Faint, permanent (not hover-only) glow per status -- night-glow pass.
// Deliberately much softer than Button's hover glow (larger blur radius,
// tighter spread, lower-alpha color) since a badge glows *at rest* to
// read as "this status is alive," not as an interactive affordance like
// a button's hover state. No entry for the neutral/unknown fallback --
// a status this app doesn't recognize shouldn't glow as if it means
// something.
const STATUS_GLOW = {
  pending: "shadow-[0_0_12px_-2px_rgba(245,165,36,0.35)]",
  running: "shadow-[0_0_12px_-2px_rgba(245,165,36,0.35)]",
  succeeded: "shadow-[0_0_12px_-2px_rgba(16,185,129,0.35)]",
  failed: "shadow-[0_0_12px_-2px_rgba(239,87,87,0.35)]",
};

const NEUTRAL_STYLE = "border-neutral-600 bg-neutral-800 text-neutral-400";

const PULSING_STATUSES = new Set(["pending", "running"]);

export default function Badge({ status, className = "" }) {
  const pulsing = PULSING_STATUSES.has(status);
  const style = STATUS_STYLES[status] ?? NEUTRAL_STYLE;
  const glow = STATUS_GLOW[status] ?? "";

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium capitalize ${style} ${glow} ${className}`}
    >
      {pulsing && <PulseDot />}
      {status}
    </span>
  );
}

export function PulseDot({ className = "" }) {
  return (
    <motion.span
      animate={{ opacity: [1, 0.3, 1] }}
      transition={{ duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
      className={`h-1.5 w-1.5 rounded-full bg-current ${className}`}
    />
  );
}
