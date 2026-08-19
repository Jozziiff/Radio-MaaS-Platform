"""Fetch Orange Tunisie cell tower data from OpenCelliD (M6.75).

Standalone, one-time script -- NOT part of the running FastAPI app, not
imported by main.py. Run by hand whenever the cached tower data needs
refreshing:

    OPENCELLID_API_KEY=... python scripts/fetch_towers.py

Queries OpenCelliD's area/bounding-box endpoint (verified directly against
https://wiki.opencellid.org/wiki/API on 2026-08-18, not assumed from prior
knowledge -- the real endpoint is GET /cell/getInArea, not a REST-ish path
like /area or /towers some other cell-tower APIs use) for a box covering
greater Tunis, filtered to mcc=605 (Tunisia) and mnc=1 (Orange Tunisie --
Tunisie Telecom is mnc=2, Ooredoo is mnc=3). Writes the result to
services/frontend/src/data/orange_towers.json for the frontend to read as
a static asset.

Data license: OpenCelliD's database is CC BY-SA 4.0 -- crowdsourced, not
Orange's proprietary infrastructure data, and not guaranteed accurate or
complete. Wherever this data is displayed, "Data: OpenCelliD" (or
equivalent) must appear visibly, per the license's attribution
requirement. See docs/decisions/ for the full write-up of this
constraint and what it does/doesn't mean for how this data can be used.
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

OPENCELLID_BASE_URL = "https://www.opencellid.org/cell/getInArea"

# Roughly covers greater Tunis (city proper plus nearby suburbs/lac area) --
# BBOX format confirmed as "latmin,lonmin,latmax,lonmax" against the live
# API docs, not assumed.
TUNIS_BBOX = (36.65, 10.05, 36.95, 10.35)

MCC_TUNISIA = 605
MNC_ORANGE_TUNISIE = 1

# The API's own documented maximum -- requesting more than this per call
# is not possible; pagination via `offset` is required to get everything
# in a page of towers.
PAGE_SIZE = 50

# The API rejects any single BBOX larger than 4,000,000 sq meters (its own
# error message: "BBOX too big - Limit to 4,000,000 sq.mts.") -- discovered
# by actually hitting that error against the real API, not documented on
# the wiki page itself. TUNIS_BBOX alone is roughly 33km x 27km, far over
# that cap, so it has to be tiled into a grid of smaller boxes queried
# separately. 0.018 degrees square was confirmed against the live API to
# fit under the cap (~2.0km x 1.6km at this latitude, ~3.2M sq m); 0.02
# was confirmed to still be rejected. Kept well under the boundary rather
# than exactly at it, since the API measures actual great-circle area, not
# a simple degree count, and longitude degrees shrink faster the further
# this ever gets adapted to run at a different latitude.
TILE_SIZE_DEG = 0.018

# Fixed delay between every request (tile-to-tile and page-to-page alike)
# -- this script runs once by hand, not on a schedule, so a small fixed
# delay costs nothing and keeps well clear of hammering the API, on top
# of already being far under the documented 1000-requests/day cap for the
# whole run (see the tile-count log line printed at the start of a run).
REQUEST_DELAY_SECONDS = 0.3

OUTPUT_PATH = (
    Path(__file__).parent.parent.parent / "frontend" / "src" / "data" / "orange_towers.json"
)

# Sidecar progress file, not committed (see .gitignore) -- records which
# tile indices have already been fetched successfully, so a run
# interrupted by the API's daily request quota (discovered the hard way:
# the wiki page says 1000 requests/day, the live API actually enforces
# 5000 and still isn't enough for one uninterrupted run of this bbox on a
# fresh key) can resume tomorrow without re-querying tiles already done
# and burning quota on them twice. Deleted automatically once a run
# finishes every tile with nothing left to fetch.
PROGRESS_PATH = Path(__file__).parent / ".fetch_towers_progress.json"


class OpenCelliDError(RuntimeError):
    """Raised when the OpenCelliD API is unreachable or returns an error."""


# The API's own "no results for this query" signal, confirmed by
# deliberately querying an empty corner of the Tunis bbox: a 200 response
# with body {"error": "No cells found", "code": 1}. Since a big bbox has
# to be split into many small tiles (see TILE_SIZE_DEG), most individual
# tiles legitimately have zero Orange towers in them (parks, the lake,
# low-density areas) -- this is the normal, expected case for a tile, not
# a real failure, and must not abort the whole run the way every *other*
# error code does (bad key, oversized BBOX, daily limit exceeded, etc.).
NO_CELLS_FOUND_CODE = 1


def fetch_page(api_key: str, bbox: tuple[float, float, float, float], offset: int) -> list[dict[str, Any]]:
    """Fetch one page (up to PAGE_SIZE cells) of the area query.

    Args:
        api_key: OpenCelliD API key.
        bbox: (latmin, lonmin, latmax, lonmax).
        offset: Pagination offset, per the API's own `offset` parameter.

    Returns:
        The raw list of cell records from this page (possibly empty).

    Raises:
        OpenCelliDError: on a network failure, a non-200 response, or a
            response that isn't valid JSON -- callers must not silently
            treat any of these as "zero towers."
    """
    latmin, lonmin, latmax, lonmax = bbox
    params = {
        "key": api_key,
        "BBOX": f"{latmin},{lonmin},{latmax},{lonmax}",
        "mcc": MCC_TUNISIA,
        "mnc": MNC_ORANGE_TUNISIE,
        "limit": PAGE_SIZE,
        "offset": offset,
        "format": "json",
    }

    try:
        response = requests.get(OPENCELLID_BASE_URL, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        raise OpenCelliDError(f"could not reach OpenCelliD: {exc}") from exc

    if response.status_code != 200:
        raise OpenCelliDError(
            f"OpenCelliD returned status {response.status_code}: {response.text[:500]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise OpenCelliDError(
            f"OpenCelliD response was not valid JSON: {response.text[:500]}"
        ) from exc

    # The API reports its own errors as a 200 with an {"error": ..., "code":
    # ...} body (e.g. a bad key, an oversized BBOX, or the daily 1000
    # request limit), not always as a non-200 status -- must be checked
    # explicitly, not inferred from the HTTP status alone. Confirmed the
    # actual key is "error" (not "err") by deliberately triggering a real
    # API error (an oversized BBOX) and inspecting the live response --
    # an earlier version of this script checked for "err" and silently
    # treated a genuine API error as "zero towers found," exactly the
    # silent-failure mode this script is supposed to avoid.
    #
    # code == NO_CELLS_FOUND_CODE is the one error that means "this
    # specific query legitimately found nothing" rather than a real
    # problem -- every other code (bad key, oversized BBOX, daily limit,
    # etc.) still raises and aborts the run.
    if isinstance(body, dict) and "error" in body:
        if body.get("code") == NO_CELLS_FOUND_CODE:
            return []
        raise OpenCelliDError(f"OpenCelliD API error ({body.get('code')}): {body['error']}")

    cells = body.get("cells", []) if isinstance(body, dict) else []
    return cells


def fetch_all_in_tile(api_key: str, tile: tuple[float, float, float, float]) -> list[dict[str, Any]]:
    """Page through one tile's area query until a short/empty page ends it.

    A page shorter than PAGE_SIZE means it was the last page (the API has
    no separate "total count" field to check against instead).
    """
    tile_cells: list[dict[str, Any]] = []
    offset = 0

    while True:
        page = fetch_page(api_key, tile, offset)
        tile_cells.extend(page)

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE
        time.sleep(REQUEST_DELAY_SECONDS)

    return tile_cells


def build_tiles(bbox: tuple[float, float, float, float], tile_size_deg: float) -> list[tuple[float, float, float, float]]:
    """Split a bounding box into a grid of tiles no larger than tile_size_deg square.

    Required because the API rejects any single BBOX over ~4,000,000 sq
    meters (see TILE_SIZE_DEG's comment) -- TUNIS_BBOX alone is far over
    that, so it can only ever be queried as many smaller boxes, not one
    request.
    """
    latmin, lonmin, latmax, lonmax = bbox
    tiles = []

    lat = latmin
    while lat < latmax:
        lat_end = min(lat + tile_size_deg, latmax)
        lon = lonmin
        while lon < lonmax:
            lon_end = min(lon + tile_size_deg, lonmax)
            tiles.append((lat, lon, lat_end, lon_end))
            lon = lon_end
        lat = lat_end

    return tiles


def load_progress() -> dict[str, Any]:
    """Load a prior partial run's progress, or a fresh empty structure if none exists."""
    if not PROGRESS_PATH.exists():
        return {"completed_tile_indices": [], "cells": []}
    return json.loads(PROGRESS_PATH.read_text())


def save_progress(completed_tile_indices: list[int], cells: list[dict[str, Any]]) -> None:
    """Persist progress after every tile -- not just at the end -- so an
    interrupted run (a quota error, a network drop, Ctrl-C) loses at most
    one in-flight tile's work, not everything fetched so far.
    """
    PROGRESS_PATH.write_text(
        json.dumps({"completed_tile_indices": completed_tile_indices, "cells": cells})
    )


def fetch_all(api_key: str, bbox: tuple[float, float, float, float]) -> tuple[list[dict[str, Any]], bool]:
    """Tile the bounding box and fetch every cell across all tiles, deduplicated.

    Resumable: tiles already recorded in PROGRESS_PATH from a prior
    interrupted run are skipped entirely, not re-queried -- important
    since a query that already succeeded still counts against the API's
    daily request quota if repeated for no reason.

    Adjacent tiles can both return a cell sitting near their shared edge
    (the API returns any cell whose location falls inside the queried
    box, and a single point can be inside more than one tile's box only
    if the tiles overlap -- they don't here, but a cell exactly on a
    shared boundary line can still be returned by both sides depending on
    how the API treats an inclusive edge). Deduplicated by cellid, which
    OpenCelliD documents as a unique identifier per cell.

    Returns:
        (cells, complete) -- complete is False if an OpenCelliDError cut
        the run short partway through the tile grid; the caller decides
        what to do with a partial result instead of this function
        silently treating "ran out of quota" the same as "actually done."
    """
    tiles = build_tiles(bbox, TILE_SIZE_DEG)

    progress = load_progress()
    completed_indices: set[int] = set(progress["completed_tile_indices"])
    all_cells: list[dict[str, Any]] = progress["cells"]
    seen_cell_ids: set[Any] = {cell.get("cellid") for cell in all_cells}

    remaining = len(tiles) - len(completed_indices)
    if completed_indices:
        print(
            f"Resuming from a prior run: {len(completed_indices)}/{len(tiles)} tiles "
            f"already fetched ({len(all_cells)} cells so far), {remaining} tiles left"
        )
    print(f"Bounding box split into {len(tiles)} tiles (each <= {TILE_SIZE_DEG}° square)")

    completed_indices_list = list(completed_indices)

    for i, tile in enumerate(tiles, start=1):
        if i in completed_indices:
            continue

        try:
            tile_cells = fetch_all_in_tile(api_key, tile)
        except OpenCelliDError as exc:
            print(f"Fetch failed on tile {i}/{len(tiles)}: {exc}", file=sys.stderr)
            save_progress(completed_indices_list, all_cells)
            return all_cells, False

        new_count = 0
        for cell in tile_cells:
            cell_id = cell.get("cellid")
            if cell_id in seen_cell_ids:
                continue
            seen_cell_ids.add(cell_id)
            all_cells.append(cell)
            new_count += 1

        if tile_cells:
            print(f"  tile {i}/{len(tiles)}: {len(tile_cells)} cells ({new_count} new)")

        completed_indices_list.append(i)
        save_progress(completed_indices_list, all_cells)

        time.sleep(REQUEST_DELAY_SECONDS)

    return all_cells, True


def to_output_row(cell: dict[str, Any]) -> dict[str, Any]:
    """Map one raw OpenCelliD cell record to this app's lean output shape.

    Drops everything the API returns besides these six fields (mcc, mnc,
    lac, averageSignalStrength, changeable, and any radio-specific extras
    like rnc/cid/tac): the frontend's map only needs enough to place a
    marker and describe it. mcc/mnc are redundant once every row in the
    file is already known to be mcc=605/mnc=1 (the query itself
    guarantees that), and averageSignalStrength/changeable/exact per-cell
    identifiers aren't used anywhere in this app.
    """
    return {
        "cell_id": cell.get("cellid"),
        "lat": cell.get("lat"),
        "lon": cell.get("lon"),
        "range_m": cell.get("range"),
        "radio": cell.get("radio"),
        "samples": cell.get("samples"),
    }


def export_partial() -> None:
    """Write out the current checkpoint's cells as-is, without fetching anything.

    For using a partial fetch (e.g. stopped by the daily quota with tiles
    still left) as real, usable map data now instead of waiting for a
    100%-complete run -- the checkpoint's cells are already deduplicated
    by cellid (see fetch_all's seen_cell_ids), so this just maps them
    through to_output_row and writes OUTPUT_PATH the same way a complete
    run would.

    Deliberately does NOT delete PROGRESS_PATH the way a complete run
    does -- the fetch is still genuinely incomplete, and a later "top up"
    run needs PROGRESS_PATH intact to resume from the next unfetched tile
    instead of starting over. Safe to run this and later resume fetching:
    the two operations don't conflict, since resuming only ever appends
    more cells to the same checkpoint this reads from.
    """
    if not PROGRESS_PATH.exists():
        print(f"No progress file at {PROGRESS_PATH} -- nothing to export.", file=sys.stderr)
        sys.exit(1)

    progress = load_progress()
    cells = progress["cells"]
    completed = len(progress["completed_tile_indices"])
    tiles = build_tiles(TUNIS_BBOX, TILE_SIZE_DEG)

    if not cells:
        print("Progress file exists but has zero cells -- nothing to export.", file=sys.stderr)
        sys.exit(1)

    rows = [to_output_row(cell) for cell in cells]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(rows, indent=2))

    print(
        f"Exported partial checkpoint: {len(rows)} towers from {completed}/{len(tiles)} "
        f"tiles written to {OUTPUT_PATH}. {PROGRESS_PATH} left in place -- run this "
        f"script again (without --export-partial) later to top up the remaining tiles."
    )


def main() -> None:
    """Fetch every Orange Tunisie tower in the Tunis bounding box and write the cache file.

    A run cut short by the API's daily quota (or any other OpenCelliDError
    mid-run) still writes out whatever was collected, clearly marked
    incomplete -- silently discarding real progress just because the run
    didn't finish in one sitting would mean re-fetching already-successful
    tiles tomorrow and wasting quota on them twice. The next run picks up
    automatically from PROGRESS_PATH.

    `--export-partial` bypasses fetching entirely and just writes out
    whatever's already checkpointed -- see export_partial().
    """
    if "--export-partial" in sys.argv:
        export_partial()
        return

    api_key = os.environ.get("OPENCELLID_API_KEY")
    if not api_key:
        print("OPENCELLID_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    print(
        f"Querying OpenCelliD: bbox={TUNIS_BBOX}, mcc={MCC_TUNISIA}, mnc={MNC_ORANGE_TUNISIE}"
    )

    raw_cells, complete = fetch_all(api_key, TUNIS_BBOX)
    count = len(raw_cells)

    # Explicit, loud signal on a suspiciously small result -- silently
    # writing a near-empty file (or worse, falling back to an unfiltered
    # query) would look like success while actually meaning the bbox or
    # mnc filter is wrong. There is no "correct" nonzero threshold known
    # in advance, so this doesn't guess one; it just refuses to pretend
    # a near-empty result is fine. Only applies to a *complete* run with
    # zero results -- a partial run with zero results so far just hasn't
    # reached a populated tile yet, which isn't the same signal.
    if complete and count == 0:
        print(
            "No towers found for mcc=605, mnc=1 in this bounding box. "
            "Not writing an empty/fallback file -- check the bbox and mnc "
            "filter before re-running.",
            file=sys.stderr,
        )
        sys.exit(1)

    rows = [to_output_row(cell) for cell in raw_cells]

    # OUTPUT_PATH's shape is always a flat array, complete or not -- a
    # consumer (the frontend) should never have to branch on two possible
    # JSON shapes for the same file. Incompleteness is signaled by NOT
    # writing OUTPUT_PATH at all on a partial run (the last known-good
    # complete file, if any, is left untouched) plus PROGRESS_PATH
    # existing on disk -- that combination is the actual signal a partial
    # run happened, not a field inside the data file itself.
    if not complete:
        print(
            f"Fetch incomplete -- collected {count} towers so far but did not "
            f"finish the bounding box (see the error above). NOT overwriting "
            f"{OUTPUT_PATH} with a partial result. Progress is saved in "
            f"{PROGRESS_PATH} -- run this script again to resume and finish.",
            file=sys.stderr,
        )
        sys.exit(1)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(rows, indent=2))
    PROGRESS_PATH.unlink(missing_ok=True)

    print(f"Wrote {count} towers to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
