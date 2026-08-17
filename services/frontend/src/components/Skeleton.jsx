// Skeleton (M6, design pass): the pulsing placeholder block pattern,
// previously inlined separately in CatalogPage and HistoryPage (same
// animation values, copy-pasted) and entirely absent from MacroForm's
// edit-load wait, which just showed plain "Loading…" text instead --
// replaced here so every loading state in the app uses the same visual
// language.

import { motion } from "motion/react";

export default function Skeleton({ count = 1, height = "h-12", className = "", stacked = true }) {
  const blocks = Array.from({ length: count }, (_, i) => (
    <motion.div
      key={i}
      animate={{ opacity: [0.4, 0.8, 0.4] }}
      transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut", delay: i * 0.15 }}
      className={`rounded-xl border border-signal-700 bg-signal-900 ${height}`}
    />
  ));

  if (count === 1) return blocks[0] && <div className={className}>{blocks[0]}</div>;

  return <div className={`${stacked ? "space-y-2" : "grid gap-3 sm:grid-cols-2"} ${className}`}>{blocks}</div>;
}
