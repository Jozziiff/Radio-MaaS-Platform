// Macro icon set (M6): mirrors db.VALID_ICONS on the backend exactly --
// this is the one place that mapping lives on the frontend, so the icon
// picker (NewMacroForm/EditMacroForm) and the catalog cards
// (CatalogPage) both render the same lucide-react component for a given
// icon name instead of each keeping their own copy.
//
// If the backend's VALID_ICONS set ever changes, this needs updating too
// -- there's no runtime fetch of the valid set, it's small and stable
// enough that a fixed mirror is simpler than an extra API round trip.

import { Signal, Activity, Database, BarChart3, Zap, Radio, Waves, Gauge, PhoneCall, TrendingUp } from "lucide-react";

export const ICON_OPTIONS = [
  "signal",
  "activity",
  "database",
  "bar-chart",
  "zap",
  "radio",
  "waves",
  "gauge",
  "phone-call",
  "trending-up",
];

const ICON_COMPONENTS = {
  signal: Signal,
  activity: Activity,
  database: Database,
  "bar-chart": BarChart3,
  zap: Zap,
  radio: Radio,
  waves: Waves,
  gauge: Gauge,
  "phone-call": PhoneCall,
  "trending-up": TrendingUp,
};

export function iconComponentFor(iconName) {
  return ICON_COMPONENTS[iconName] ?? Signal;
}
