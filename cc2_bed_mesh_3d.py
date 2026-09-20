#!/usr/bin/env python3
"""
cc2_bed_mesh_3d.py
-------------------
Extracts the Elegoo Centauri Carbon 2 bed-mesh height map from a captured
MQTT traffic dump (the JSONL file produced by cc2_mesh_sniffer.py, one
JSON object per line: {"ts":..., "topic":..., "payload":{...}}).

The CC2 doesn't publish a single "here's your mesh" message. Instead,
during Auto Bed Leveling it streams incremental gcode_move.x/y/z position
updates as it visits each probe point on the bed (a regular grid - 11x11 =
121 points on stock hardware). This script:

  1. Reconstructs the full (x, y, z) trajectory from the delta updates.
  2. Groups consecutive samples into "visits" to each distinct grid point.
  3. Within each visit, discards the travel/retract Z heights (large,
     e.g. 3-10mm) and keeps the small, settled contact reading(s) -
     that's the probed mesh height at that point.
  4. Auto-detects which machine_status.sub_status phase corresponds to
     the *full* mesh pass (the one that produces the most, most-regular
     grid points), so it isn't hardcoded to one firmware's status codes.
  5. Renders a 2D heatmap (annotated with the peak-to-valley range) and an
     interactive, rotatable 3D surface page, and saves the grid as CSV.

Usage:
    python cc2_bed_mesh_3d.py cc2_raw_dump.jsonl

Output (next to the input file, or via --output-dir):
    mesh_grid.csv             - the height grid as plain numbers (mm)
    mesh_heatmap.png           - 2D top-down heatmap with values + peak-to-valley
    mesh_3d_interactive.html   - rotatable 3D surface (open in a browser)

Requirements:
    pip install numpy matplotlib
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("Missing dependency. Install with: pip install numpy matplotlib")


CONTACT_THRESHOLD_MM = 1.5  # Z readings smaller than this (abs) are treated
                            # as "settled on the bed"; travel/retract moves
                            # on stock CC2 firmware are always >= 3mm.


def load_events(path):
    """Replay the JSONL dump and reconstruct the full (x, y, z,
    sub_status) trajectory over time from the delta updates."""
    state = {"x": None, "y": None, "z": None}
    cur_sub = None
    events = []  # (x, y, z, sub_status)

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not obj.get("topic", "").endswith("api_status"):
                continue
            payload = obj.get("payload")
            if not isinstance(payload, dict):
                continue
            result = payload.get("result", {})

            ms = result.get("machine_status")
            if isinstance(ms, dict) and "sub_status" in ms:
                cur_sub = ms["sub_status"]

            gm = result.get("gcode_move")
            if not isinstance(gm, dict):
                continue
            state.update({k: v for k, v in gm.items() if k in ("x", "y", "z")})
            x, y, z = state["x"], state["y"], state["z"]
            if x is None or y is None or z is None:
                continue
            events.append((x, y, z, cur_sub))

    return events


def group_visits(events):
    """Group consecutive events into 'visits': runs where the (rounded)
    x,y position stays the same. Returns a dict: sub_status -> list of
    visits, where each visit is {'gx', 'gy', 'zs': [...]}."""
    by_sub = {}
    last_key = None
    last_sub = None
    cur_visit = None

    for x, y, z, sub in events:
        key = (round(x), round(y))
        if key != last_key or sub != last_sub:
            cur_visit = {"gx": key[0], "gy": key[1], "zs": []}
            by_sub.setdefault(sub, []).append(cur_visit)
            last_key = key
            last_sub = sub
        cur_visit["zs"].append(z)

    return by_sub


def extract_grid(visits):
    """From a list of visits (all belonging to one sub_status phase),
    build a {(x, y): z} height map using the settled contact reading of
    each visit. Returns (points_dict, num_distinct_points)."""
    pts = {}
    for v in visits:
        contact_zs = [z for z in v["zs"] if abs(z) < CONTACT_THRESHOLD_MM]
        if not contact_zs:
            continue
        final_z = contact_zs[-1]
        key = (v["gx"], v["gy"])
        pts.setdefault(key, []).append(final_z)

    final = {k: statistics.mean(vals) for k, vals in pts.items()}
    return final


def looks_like_regular_grid(points_dict):
    """Score how 'grid-like' a set of points is: a real bed mesh forms a
    roughly regular rectangular grid, so #distinct_x * #distinct_y should
    be close to the number of points actually found."""
    if len(points_dict) < 9:  # smaller than 3x3 isn't a mesh pass
        return 0
    xs = sorted(set(k[0] for k in points_dict))
    ys = sorted(set(k[1] for k in points_dict))
    expected = len(xs) * len(ys)
    coverage = len(points_dict) / expected if expected else 0
    # Prefer phases with many points AND high grid coverage.
    return len(points_dict) * coverage


def find_best_mesh_phase(events):
    by_sub = group_visits(events)
    best_sub, best_points, best_score = None, None, -1
    for sub, visits in by_sub.items():
        pts = extract_grid(visits)
        score = looks_like_regular_grid(pts)
        if score > best_score:
            best_sub, best_points, best_score = sub, pts, score
    return best_sub, best_points


def to_arrays(points_dict):
    xs = sorted(set(k[0] for k in points_dict))
    ys = sorted(set(k[1] for k in points_dict))
    grid = np.full((len(ys), len(xs)), np.nan)
    for (x, y), z in points_dict.items():
        grid[ys.index(y), xs.index(x)] = z
    return np.array(xs), np.array(ys), grid


def plot_heatmap(xs, ys, grid, out_path):
    finite = grid[~np.isnan(grid)]
    peak_to_valley = finite.max() - finite.min()

    fig, ax = plt.subplots(figsize=(8, 7))
    im = ax.imshow(grid, cmap="coolwarm", origin="lower",
                    extent=[xs.min(), xs.max(), ys.min(), ys.max()],
                    aspect="equal")
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            val = grid[j, i]
            if not np.isnan(val):
                ax.text(x, y, f"{val:.2f}", ha="center", va="center", fontsize=6)
    ax.set_title(
        "Elegoo Centauri Carbon 2 - Bed Mesh Heatmap (mm)\n"
        f"Min: {finite.min():.3f}  Max: {finite.max():.3f}  "
        f"Peak-to-valley: {peak_to_valley:.3f} mm"
    )
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    fig.colorbar(im, ax=ax, label="Z offset (mm)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


PLOTLY_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CC2 Bed Mesh (3D)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.27.0/plotly.min.js"></script>
<style>
  body {{ margin:0; font-family: -apple-system, Segoe UI, Roboto, sans-serif;
         background:#f8f9fb; color:#1a1a1a; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#14161a; color:#eaecef; }}
  }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 20px 16px 40px; }}
  .stats {{ display:flex; gap:12px; flex-wrap:wrap; margin: 10px 0 16px; }}
  .stat {{ background: rgba(128,128,128,0.1); border-radius: 10px;
           padding: 8px 14px; font-size: 0.85rem; }}
  .stat b {{ font-size: 1rem; }}
  #plot {{ width: 100%; height: 620px; }}
  .hint {{ color:#8a8f98; font-size:0.85rem; text-align:center; margin-top:10px; }}
</style>
</head>
<body>
<div class="wrap">
  <h2>Elegoo Centauri Carbon 2 &mdash; Сетка стола / Bed mesh (3D)</h2>
  <div class="stats">
    <div class="stat">Min: <b>{zmin:.3f} mm</b></div>
    <div class="stat">Max: <b>{zmax:.3f} mm</b></div>
    <div class="stat">Peak-to-valley: <b>{ptv:.3f} mm</b></div>
  </div>
  <div id="plot"></div>
  <p class="hint">Drag to rotate &middot; scroll to zoom &middot; right-drag to pan</p>
</div>
<script>
const xs = {xs_json};
const ys = {ys_json};
const z = {z_json};
const zeroPlane = z.map(row => row.map(() => 0));
const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;

Plotly.newPlot('plot', [
  {{
    type: 'surface', x: xs, y: ys, z: z,
    colorscale: 'RdBu', reversescale: true,
    colorbar: {{ title: 'mm' }},
    name: 'Bed mesh'
  }},
  {{
    type: 'surface', x: xs, y: ys, z: zeroPlane,
    opacity: 0.25,
    colorscale: [[0, 'rgb(160,160,160)'], [1, 'rgb(160,160,160)']],
    showscale: false,
    hoverinfo: 'skip',
    name: 'Zero plane'
  }}
], {{
  paper_bgcolor: isDark ? '#1c1f26' : '#f8f9fb',
  plot_bgcolor: isDark ? '#1c1f26' : '#f8f9fb',
  font: {{ color: isDark ? '#eaecef' : '#1a1a1a' }},
  margin: {{ l:0, r:0, t:10, b:0 }},
  scene: {{
    xaxis: {{ title: 'X (mm)' }}, yaxis: {{ title: 'Y (mm)' }}, zaxis: {{ title: 'Z (mm)' }},
    aspectratio: {{ x:1, y:1, z:0.35 }}
  }}
}}, {{responsive: true, displaylogo: false}});
</script>
</body>
</html>
"""


def save_interactive_html(xs, ys, grid, out_path):
    import json as _json
    z_list = [[None if np.isnan(v) else round(float(v), 4) for v in row] for row in grid]
    finite = grid[~np.isnan(grid)]
    html = PLOTLY_HTML_TEMPLATE.format(
        xs_json=_json.dumps([int(x) for x in xs]),
        ys_json=_json.dumps([int(y) for y in ys]),
        z_json=_json.dumps(z_list),
        zmin=finite.min(),
        zmax=finite.max(),
        ptv=finite.max() - finite.min(),
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


def save_csv(xs, ys, grid, out_path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("y\\x," + ",".join(str(x) for x in xs) + "\n")
        for j, y in enumerate(ys):
            row = ",".join(f"{v:.4f}" if not np.isnan(v) else "" for v in grid[j])
            f.write(f"{y},{row}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to cc2_raw_dump.jsonl")
    parser.add_argument("--output-dir", default=None,
                         help="Where to write outputs (default: alongside input)")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.output_dir) if args.output_dir else in_path.parent

    print(f"[+] Loading {in_path} ...")
    events = load_events(in_path)
    print(f"[+] Reconstructed {len(events)} position samples")

    sub, points = find_best_mesh_phase(events)
    if not points or len(points) < 9:
        sys.exit("[!] Could not find a plausible bed-mesh grid in this dump. "
                  "Make sure the capture covers a full Auto Bed Leveling run.")

    xs = sorted(set(k[0] for k in points))
    ys = sorted(set(k[1] for k in points))
    print(f"[+] Best mesh phase: sub_status={sub}, "
          f"{len(points)} points, grid {len(xs)}x{len(ys)}")

    xs_arr, ys_arr, grid = to_arrays(points)

    csv_path = out_dir / "mesh_grid.csv"
    heatmap_path = out_dir / "mesh_heatmap.png"
    interactive_path = out_dir / "mesh_3d_interactive.html"

    save_csv(xs_arr, ys_arr, grid, csv_path)
    plot_heatmap(xs_arr, ys_arr, grid, heatmap_path)
    save_interactive_html(xs_arr, ys_arr, grid, interactive_path)

    finite = grid[~np.isnan(grid)]
    print(f"[+] Z range: {finite.min():.3f} .. {finite.max():.3f} mm "
          f"(peak-to-valley: {finite.max() - finite.min():.3f} mm)")
    print(f"[+] Saved: {csv_path}")
    print(f"[+] Saved: {heatmap_path}  (peak-to-valley annotated in title)")
    print(f"[+] Saved: {interactive_path}  (open in a browser to rotate/zoom)")


if __name__ == "__main__":
    main()
