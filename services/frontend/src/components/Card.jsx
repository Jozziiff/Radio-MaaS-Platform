// Card (M6, design pass): the bordered-panel recipe repeated across the
// app (macro cards, dialogs, login form, history table, skeletons) --
// two levels: "default" (neutral signal-700 border) and "accent" (the
// amber-tinted border + shadow used for the catalog's expanded
// create/edit/run panel, the one surface meant to visually pop).
//
// `as` picks the rendered element ("div" by default, "form" for
// LoginPage's login form so native form submit/validation still works).
// Not a motion.div itself -- CatalogPage's expand-in-place animation
// (layoutId, layout, AnimatePresence) stays owned by CatalogPage, which
// wraps its own motion.div around this component's classes via
// `cardClasses()` when it needs the animated variant.

const LEVELS = {
  default: "border-signal-700 bg-signal-900",
  accent: "border-amber-500/40 bg-signal-900 shadow-xl shadow-black/30",
};

export default function Card({ as: Tag = "div", level = "default", className = "", children, ...props }) {
  return (
    <Tag className={`rounded-xl border ${LEVELS[level]} ${className}`} {...props}>
      {children}
    </Tag>
  );
}

export function cardClasses(level = "default") {
  return `rounded-xl border ${LEVELS[level]}`;
}
