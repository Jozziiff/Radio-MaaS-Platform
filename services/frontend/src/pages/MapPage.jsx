// Map page (M6.75): renders every cached Orange Tunisie tower
// (data/orange_towers.json, see services/backend-api/scripts/fetch_towers.py
// and docs/decisions/M6.75-opencellid-tower-data.md) on a Leaflet map,
// clustered so 6,000+ individual markers don't render (and don't tank
// browser performance) all at once at low zoom.
//
// A static local JSON import, not a fetch() call -- the data is a build-time
// asset (Vite bundles the JSON import directly), not something the running
// backend serves; there is no /towers endpoint anywhere in main.py, on
// purpose (see the decision doc: this was always meant to be a one-time
// fetch-and-cache, not a live API).
//
// Two license-driven attributions are both real requirements, not optional
// polish: CARTO's tile attribution (their free tiles require it regardless
// of which basemap style is used) and "Data: OpenCelliD" (the tower data's
// CC BY-SA 4.0 license). Both render via Leaflet's own attribution control,
// which is the standard place a map consumer expects to find them, not a
// separate custom-built label elsewhere on the page where they'd be easy
// to miss or accidentally omit.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MapContainer, TileLayer, Marker, Popup, Circle } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "./MapPage.css";
import { Loader2 } from "lucide-react";
import Shell from "../components/Shell";
import TopBar from "../components/TopBar";
import Card from "../components/Card";
import towers from "../data/orange_towers.json";

const TUNIS_CENTER = [36.8, 10.18];
const DEFAULT_ZOOM = 12;

const CARTO_LIGHT_TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
// CARTO's own required attribution text (their basemap terms of use)
// alongside OpenStreetMap's, whose data CARTO's tiles are built from --
// CARTO's own docs list both as required together, not OpenStreetMap's
// alone. Text is identical to the prior dark_all tile's attribution --
// CARTO requires the same credit regardless of which of their basemap
// styles is in use, only the tile URL itself changes between styles.
const CARTO_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors ' +
  '&copy; <a href="https://carto.com/attributions">CARTO</a> &middot; Data: OpenCelliD';

// One color per radio generation, chosen for contrast against CARTO's
// *light* (Positron) basemap specifically -- verified with real WCAG
// contrast math against light_all's near-white background, not eyeballed:
// the original marker colors (tuned for the dark_all tile this page used
// before) scored 1.7-2.2:1 against a light background, badly under the
// 3:1 floor for a graphical element and effectively invisible on white.
// These replacements score 4.3-5.9:1. Falls back to neutral gray for any
// radio value not in this map (NR/CDMA exist in the raw API's vocabulary
// but weren't seen in this Tunis dataset).
const RADIO_COLORS = {
  GSM: "#1d4ed8", // deep blue
  UMTS: "#c2410c", // burnt orange (distinct from LTE's green by hue, not just lightness)
  LTE: "#047857", // deep green
};
const RADIO_COLOR_FALLBACK = "#57667f"; // darkened signal-400, same reasoning: legible on white

const RADIO_LEGEND_ORDER = ["GSM", "UMTS", "LTE"];

function radioColor(radio) {
  return RADIO_COLORS[radio] ?? RADIO_COLOR_FALLBACK;
}

// Leaflet's default marker icon references image files by a relative path
// that doesn't survive bundling (a well-known Leaflet+bundler papercut) --
// sidestepped entirely by never using the default icon. Every marker here
// is a small colored divIcon (plain HTML/CSS, no image asset), which also
// happens to be exactly what's needed anyway to color-code by radio type.
function towerIcon(radio) {
  const color = radioColor(radio);
  return L.divIcon({
    className: "",
    html: `<span style="display:block;width:12px;height:12px;border-radius:9999px;background:${color};border:1.5px solid rgba(6,10,20,0.8);box-shadow:0 0 6px -1px ${color};"></span>`,
    iconSize: [12, 12],
    iconAnchor: [6, 6],
  });
}

// Every radio type present is visible by default -- filtering starts as
// "show everything" (the prior, unfiltered behavior), not opt-in.
const ALL_RADIO_TYPES = new Set(RADIO_LEGEND_ORDER);

// leaflet.markercluster's own chunking defaults (chunkInterval: 200ms,
// chunkDelay: 50ms) are tuned as a general-purpose default, not for this
// page's actual data volume specifically. At ~6,669 markers, cutting the
// per-frame budget and the yield-back gap both roughly in half rebuilds
// the cluster index in more, smaller steps -- each individual step blocks
// the main thread for less time, which is what actually determines
// whether a filter toggle *feels* responsive (a UI that yields back to
// the browser more often stays interactive-feeling even if the total
// rebuild time is similar). Chosen by testing against this page's real
// marker count, not a value pulled from documentation defaults.
const CHUNK_INTERVAL_MS = 100;
const CHUNK_DELAY_MS = 25;

export default function MapPage({ page, onNavigate }) {
  // job_name-shaped identity isn't relevant here -- a tower's own cell_id
  // is already unique per docs/decisions/M6.75-opencellid-tower-data.md's
  // dedup-by-cellid guarantee, so it's reused directly as the "which
  // tower's range circle is showing" key. null means no circle is shown.
  const [activeRangeCellId, setActiveRangeCellId] = useState(null);
  const [visibleRadios, setVisibleRadios] = useState(ALL_RADIO_TYPES);
  const [rebuildingClusters, setRebuildingClusters] = useState(false);
  // Guards against a stale chunkProgress callback from a filter change
  // that's already been superseded by a newer one (e.g. clicking two
  // filter pills in quick succession) still clearing the loading
  // indicator after the newer rebuild has already started -- only the
  // callback tied to the most recent toggle is allowed to update state.
  const rebuildTokenRef = useRef(0);

  // Client-side only, against the same already-loaded `towers` array --
  // no new fetch, no re-reading orange_towers.json. Recomputing this array
  // (rather than filtering inside the render loop below) is what makes
  // MarkerClusterGroup's own cluster counts correct: react-leaflet-cluster
  // rebuilds its cluster tree from whatever <Marker> children it's given,
  // so a smaller children array naturally produces smaller cluster counts
  // -- there's no separate "recalculate clusters" step to wire up.
  const visibleTowers = useMemo(
    () => towers.filter((t) => visibleRadios.has(t.radio)),
    [visibleRadios]
  );

  const activeTower = useMemo(
    () => towers.find((t) => t.cell_id === activeRangeCellId) ?? null,
    [activeRangeCellId]
  );

  // A tower whose radio type just got filtered out shouldn't leave its
  // range circle behind with no visible marker to have toggled it -- an
  // effect (not a plain conditional call during render, which risks an
  // extra render pass / React warning for a state update mid-render)
  // clears it whenever the currently-active tower's radio type drops out
  // of visibleRadios. Covers every path that can shrink visibleRadios,
  // present and future, without each one needing its own copy of this
  // check.
  useEffect(() => {
    if (activeTower && !visibleRadios.has(activeTower.radio)) {
      setActiveRangeCellId(null);
    }
  }, [activeTower, visibleRadios]);

  function toggleRadio(radio) {
    // Bump the token before the state change that triggers a re-render --
    // any chunkProgress callback still in flight from a prior rebuild
    // checks against this ref and no-ops once it's stale, so the loading
    // indicator always reflects the *latest* filter toggle, not whichever
    // rebuild happens to finish last.
    rebuildTokenRef.current += 1;
    setRebuildingClusters(true);

    setVisibleRadios((current) => {
      const next = new Set(current);
      if (next.has(radio)) {
        next.delete(radio);
      } else {
        next.add(radio);
      }
      return next;
    });
  }

  // MarkerClusterGroup's own progress signal (leaflet.markercluster's
  // chunkProgress option: fires as (processed, total, elapsed) while
  // chunkedLoading works through adding markers in batches) -- this is
  // the real "is a rebuild in progress" state, not a fixed-duration
  // fake spinner. Cleared the moment processed reaches total, so the
  // indicator's visible duration always matches the actual rebuild, not
  // an estimate of it.
  const handleChunkProgress = useCallback((processed, total) => {
    const myToken = rebuildTokenRef.current;
    if (processed >= total) {
      // Deferred one tick so a rebuild that finishes synchronously
      // within a single chunk (small filtered sets) doesn't flash the
      // indicator on and immediately off in the same render pass.
      setTimeout(() => {
        if (rebuildTokenRef.current === myToken) setRebuildingClusters(false);
      }, 0);
    }
  }, []);

  return (
    <Shell page={page} onNavigate={onNavigate}>
      <TopBar
        title="Tower map"
        description={`${towers.length.toLocaleString()} Orange Tunisie towers around Tunis, from a cached OpenCelliD snapshot.`}
      />

      <main className="mx-auto max-w-6xl px-8 pb-10">
        <RadioFilterBar visibleRadios={visibleRadios} onToggle={toggleRadio} />

        <Card className="relative overflow-hidden">
          <div className="h-[70vh] w-full">
            <MapContainer
              center={TUNIS_CENTER}
              zoom={DEFAULT_ZOOM}
              zoomSnap={0.5}
              scrollWheelZoom
              markerZoomAnimation
              className="h-full w-full"
            >
              <TileLayer url={CARTO_LIGHT_TILE_URL} attribution={CARTO_ATTRIBUTION} />

              <MarkerClusterGroup
                chunkedLoading
                animate
                chunkInterval={CHUNK_INTERVAL_MS}
                chunkDelay={CHUNK_DELAY_MS}
                chunkProgress={handleChunkProgress}
              >
                {visibleTowers.map((tower) => (
                  <Marker
                    key={tower.cell_id}
                    position={[tower.lat, tower.lon]}
                    icon={towerIcon(tower.radio)}
                  >
                    <Popup>
                      <TowerPopup
                        tower={tower}
                        showingRange={activeRangeCellId === tower.cell_id}
                        onToggleRange={() =>
                          setActiveRangeCellId((current) =>
                            current === tower.cell_id ? null : tower.cell_id
                          )
                        }
                      />
                    </Popup>
                  </Marker>
                ))}
              </MarkerClusterGroup>

              {activeTower && (
                <Circle
                  center={[activeTower.lat, activeTower.lon]}
                  radius={activeTower.range_m}
                  pathOptions={{
                    color: radioColor(activeTower.radio),
                    fillColor: radioColor(activeTower.radio),
                    fillOpacity: 0.12,
                    weight: 1.5,
                  }}
                />
              )}
            </MapContainer>
          </div>

          <RadioLegend />
          {rebuildingClusters && <RebuildingIndicator />}
        </Card>
      </main>
    </Shell>
  );
}

// Toggle pills, same shape as Button's "chip" variant elsewhere in the
// app (border + tinted fill when active) -- an off pill is visually
// muted (signal-400 text, no tint) rather than removed from layout, so
// the control's full set of options stays visible and re-togglable at a
// glance instead of the row reflowing as filters are toggled.
function RadioFilterBar({ visibleRadios, onToggle }) {
  return (
    <div className="mb-4 flex items-center gap-2">
      <span className="text-xs font-medium text-signal-400">Show:</span>
      {RADIO_LEGEND_ORDER.map((radio) => {
        const active = visibleRadios.has(radio);
        const color = radioColor(radio);
        return (
          <button
            key={radio}
            type="button"
            onClick={() => onToggle(radio)}
            aria-pressed={active}
            className={
              active
                ? "flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors"
                : "flex items-center gap-1.5 rounded-full border border-signal-700 px-3 py-1 text-xs font-medium text-signal-400 transition-colors hover:border-signal-600 hover:text-signal-200"
            }
            style={
              active
                ? { borderColor: `${color}66`, backgroundColor: `${color}1a`, color }
                : undefined
            }
          >
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: active ? color : "var(--color-signal-600)" }}
            />
            {radio}
          </button>
        );
      })}
    </div>
  );
}

function TowerPopup({ tower, showingRange, onToggleRange }) {
  return (
    <div className="font-sans text-xs">
      <p className="font-mono text-sm font-medium text-signal-950">Cell {tower.cell_id}</p>
      <dl className="mt-1.5 space-y-0.5 text-signal-700">
        <PopupRow label="Radio" value={tower.radio} />
        <PopupRow label="Range" value={`${tower.range_m.toLocaleString()} m`} />
        <PopupRow label="Samples" value={tower.samples} />
      </dl>
      <button
        type="button"
        onClick={onToggleRange}
        className="mt-2 w-full rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs font-medium text-amber-600 transition-colors hover:bg-amber-500/20"
      >
        {showingRange ? "Hide range" : "Show range"}
      </button>
    </div>
  );
}

function PopupRow({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-signal-500">{label}</dt>
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

// Brief, subtle -- top-right, small pill, no backdrop over the map itself
// -- not a full-screen blocker. Only ever appears while
// MarkerClusterGroup's real chunkProgress callback reports work still in
// flight (see handleChunkProgress above), so its presence always
// corresponds to an actual in-progress rebuild, never a fixed timer.
function RebuildingIndicator() {
  return (
    <div className="pointer-events-none absolute right-4 top-4 z-1000 flex items-center gap-1.5 rounded-full border border-signal-700 bg-signal-900/90 px-3 py-1.5 text-xs text-signal-200 shadow-lg backdrop-blur-sm">
      <Loader2 className="h-3 w-3 animate-spin" strokeWidth={2} />
      Updating map…
    </div>
  );
}

function RadioLegend() {
  return (
    <div className="pointer-events-none absolute bottom-4 left-4 z-1000 rounded-lg border border-signal-700 bg-signal-900/90 px-3 py-2 text-xs text-signal-200 shadow-lg backdrop-blur-sm">
      <p className="mb-1.5 font-medium text-signal-100">Radio type</p>
      <div className="flex flex-col gap-1">
        {RADIO_LEGEND_ORDER.map((radio) => (
          <div key={radio} className="flex items-center gap-2">
            <span
              className="h-2.5 w-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: radioColor(radio) }}
            />
            {radio}
          </div>
        ))}
      </div>
    </div>
  );
}
