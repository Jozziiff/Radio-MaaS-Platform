// IconPicker (M6): a small grid of the fixed icon set (icons.js, mirrors
// db.VALID_ICONS on the backend), used by MacroForm for both create and
// edit. Stores and reports the icon's *name* (e.g. "signal"), not the
// component itself -- that name is exactly what POST
// /macros/{name}/build's `icon` field expects.

import { ICON_OPTIONS, iconComponentFor } from "../icons";

export default function IconPicker({ value, onChange }) {
  return (
    <div>
      <span className="mb-1.5 block text-xs font-medium text-signal-400">Icon</span>
      <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
        {ICON_OPTIONS.map((iconName) => {
          const Icon = iconComponentFor(iconName);
          const selected = iconName === value;
          return (
            <button
              key={iconName}
              type="button"
              title={iconName}
              aria-pressed={selected}
              onClick={() => onChange(iconName)}
              className={`flex aspect-square items-center justify-center rounded-lg border transition-colors ${
                selected
                  ? "border-amber-500 bg-amber-500/15 text-amber-500"
                  : "border-signal-600 bg-signal-800 text-signal-400 hover:border-signal-400 hover:text-signal-200"
              }`}
            >
              <Icon className="h-4 w-4" strokeWidth={1.75} />
            </button>
          );
        })}
      </div>
    </div>
  );
}
