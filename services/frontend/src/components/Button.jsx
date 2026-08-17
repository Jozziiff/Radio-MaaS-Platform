// Button (M6, design pass): one component covering every button recipe
// that used to be hand-copied per screen -- primary CTA (solid amber),
// secondary (outlined), danger (solid red), ghost (text-only, hover
// reveals a subtle background), ghost-danger (ghost's shape, but its
// hover state signals danger -- MacroCard's delete icon button), and
// chip (translucent amber, used for compact actions like "Run" on a
// card or "Download" on a history row).
//
// ghost-danger is its own named variant rather than `variant="ghost"`
// plus a `className` override for the hover colors: Tailwind's utility
// classes aren't guaranteed to cascade in the order they appear in a
// className string (specificity ties resolve by each utility's position
// in the generated stylesheet, not call-site order), so overriding one
// hover utility with another via className is unreliable. A distinct
// variant sidesteps the ordering question entirely.
//
// Deliberately a thin wrapper around a plain <button>, not a new
// abstraction over click handling -- every prop except `variant`/`size`
// passes straight through, so existing onClick/disabled/type usage at
// call sites needed no behavior changes, only a swap from a raw <button
// className="..."> to <Button variant="...">.
//
// Defaults to type="button", not the browser's native type="submit"
// default -- most call sites (close/edit/delete/run icon buttons) live
// inside a <form> and must NOT submit it on click. Only the actual
// submit buttons pass type="submit" explicitly.

const VARIANTS = {
  primary:
    "bg-amber-500 text-signal-950 hover:bg-amber-400 disabled:cursor-not-allowed disabled:opacity-60",
  secondary:
    "border border-signal-600 text-signal-200 hover:bg-signal-800 disabled:cursor-not-allowed disabled:opacity-60",
  danger:
    "bg-danger text-signal-950 hover:bg-danger/85 disabled:cursor-not-allowed disabled:opacity-60",
  ghost: "text-signal-400 hover:bg-signal-800 hover:text-signal-100",
  "ghost-danger": "text-signal-400 hover:bg-danger/15 hover:text-danger",
  chip: "border border-amber-500/30 bg-amber-500/10 text-amber-500 hover:border-amber-500/60 hover:bg-amber-500/15 disabled:cursor-not-allowed disabled:opacity-60",
};

const SIZES = {
  sm: "gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium",
  // Between sm/md -- RunPanel's "Download result" is a prominent action
  // (full-size icon + label) but sits in a compact status row alongside
  // other text-sm content, not a page-level CTA like the primary md size.
  compact: "gap-1.5 rounded-lg px-3 py-2 text-sm font-medium",
  md: "gap-2 rounded-lg px-4 py-2.5 text-sm font-medium",
  icon: "rounded-md p-1.5",
};

export default function Button({
  variant = "primary",
  size = "md",
  type = "button",
  className = "",
  children,
  ...props
}) {
  return (
    <button
      type={type}
      className={`inline-flex shrink-0 items-center justify-center transition-colors ${VARIANTS[variant]} ${SIZES[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}
