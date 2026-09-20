#!/usr/bin/env python3
"""
cc2_mesh_sniffer.py
--------------------
Connects to an Elegoo Centauri Carbon 2 (CC2) printer as an MQTT client
(the printer itself runs the broker on port 1883 - "inverted" architecture),
logs all traffic during a bed-leveling / calibration cycle, tries to
auto-detect the bed mesh height-map inside the JSON payloads, and plots
it as a heatmap.

Usage:
    python cc2_mesh_sniffer.py --host 192.168.1.50 --password 123456

Then trigger "Auto Bed Leveling" on the printer's touchscreen (or from
ElegooSlicer) while the script is running. When it finds a plausible
height-map array in the traffic, it will save:
    - cc2_raw_dump.jsonl   (every MQTT message seen, one JSON object per line)
    - cc2_mesh_raw.json    (the specific payload the mesh was extracted from)
    - cc2_mesh_heatmap.png (the rendered heatmap)
    - cc2_mesh.csv         (the mesh as a plain grid of numbers)

Requirements:
    pip install paho-mqtt matplotlib numpy
"""

import argparse
import json
import math
import sys
import time
import uuid
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.exit("Missing dependency. Install with: pip install paho-mqtt")

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("Missing dependency. Install with: pip install matplotlib numpy")


# ---------------------------------------------------------------------------
# Heuristics for finding a height-map / bed-mesh array inside arbitrary JSON
# ---------------------------------------------------------------------------

# Keys that CC2 firmware / community reverse-engineering has been observed
# to use for mesh data. We check for these first (case-insensitive substring
# match), then fall back to a generic "numeric array/grid" scan.
MESH_KEY_HINTS = [
    "bed_mesh_detect",
    "bed_mesh",
    "meshpoints",
    "mesh_points",
    "levelingdata",
    "leveling_data",
    "mesh",
    "leveling",
    "heightmap",
    "height_map",
    "zvalues",
    "z_values",
]

# Reasonable bounds for a bed-mesh height value in mm. Used to filter out
# unrelated numeric arrays (e.g. temperature history, timestamps).
PLAUSIBLE_Z_MIN, PLAUSIBLE_Z_MAX = -5.0, 5.0


def flatten_numeric(value):
    """Recursively flatten nested lists into a flat list of floats, or
    return None if the value is not purely numeric."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    if isinstance(value, list):
        out = []
        for item in value:
            flat = flatten_numeric(item)
            if flat is None:
                return None
            out.extend(flat)
        return out
    return None


def looks_like_mesh(flat_values):
    """Check whether a flat list of numbers plausibly represents bed-mesh
    heights: enough points to form a square-ish grid, and values in a
    sane millimeter range."""
    n = len(flat_values)
    if n < 9:  # smaller than a 3x3 mesh isn't interesting
        return False
    root = math.isqrt(n)
    if root * root != n and (root - 1) * (root - 1) != n and (root + 1) * (root + 1) != n:
        # not a perfect square and not "off by one row/col" -> still allow
        # rectangular grids by just checking the value range below
        pass
    if not all(PLAUSIBLE_Z_MIN <= v <= PLAUSIBLE_Z_MAX for v in flat_values):
        return False
    return True


def find_mesh_in_json(obj, path=""):
    """Walk a parsed JSON object looking for a key whose name hints at a
    mesh, or, failing that, any numeric array/grid that looks plausible.
    Returns (path, flat_values) or (None, None)."""
    # Pass 1: key-name hints (recursive)
    hinted = _search_by_key(obj, path)
    if hinted is not None:
        return hinted

    # Pass 2: any numeric array/grid, anywhere
    return _search_generic(obj, path)


def _search_by_key(obj, path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            new_path = f"{path}.{k}" if path else str(k)
            if any(hint in lk for hint in MESH_KEY_HINTS):
                flat = flatten_numeric(v)
                if flat and len(flat) >= 9:
                    return new_path, flat
            result = _search_by_key(v, new_path)
            if result[0] is not None:
                return result
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            result = _search_by_key(item, f"{path}[{i}]")
            if result[0] is not None:
                return result
    return None, None


def _search_generic(obj, path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else str(k)
            flat = flatten_numeric(v)
            if flat and looks_like_mesh(flat):
                return new_path, flat
            result = _search_generic(v, new_path)
            if result[0] is not None:
                return result
    elif isinstance(obj, list):
        flat = flatten_numeric(obj)
        if flat and looks_like_mesh(flat):
            return path, flat
        for i, item in enumerate(obj):
            result = _search_generic(item, f"{path}[{i}]")
            if result[0] is not None:
                return result
    return None, None


def to_grid(flat_values):
    """Reshape a flat list into the most square-ish 2D grid possible."""
    n = len(flat_values)
    best = (1, n)
    best_diff = n
    for rows in range(1, n + 1):
        if n % rows == 0:
            cols = n // rows
            diff = abs(rows - cols)
            if diff < best_diff:
                best_diff = diff
                best = (rows, cols)
    rows, cols = best
    arr = np.array(flat_values, dtype=float).reshape(rows, cols)
    return arr


# ---------------------------------------------------------------------------
# MQTT handling
# ---------------------------------------------------------------------------

class MeshSniffer:
    def __init__(self, args):
        self.args = args
        self.found = False
        self.raw_log = open("cc2_raw_dump.jsonl", "a", encoding="utf-8")
        client_id = "0cli" + uuid.uuid4().hex[:6]
        self.client = mqtt.Client(client_id=client_id)
        if args.user:
            self.client.username_pw_set(args.user, args.password)
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc != 0:
            print(f"[!] Connection failed, MQTT rc={rc} "
                  f"(4/5 usually mean bad username/password)")
            return
        print(f"[+] Connected to {self.args.host}:{self.args.port}")
        client.subscribe(self.args.topic)
        print(f"[+] Subscribed to '{self.args.topic}'. "
              f"Now start Auto Bed Leveling on the printer / slicer...")

    def on_message(self, client, userdata, msg):
        ts = datetime.now().isoformat(timespec="seconds")
        raw = msg.payload.decode("utf-8", errors="replace")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            # not JSON, just log the raw topic+payload and move on
            self.raw_log.write(json.dumps(
                {"ts": ts, "topic": msg.topic, "raw": raw}) + "\n")
            self.raw_log.flush()
            return

        self.raw_log.write(json.dumps(
            {"ts": ts, "topic": msg.topic, "payload": payload}) + "\n")
        self.raw_log.flush()

        if self.found:
            return

        path, flat_values = find_mesh_in_json(payload)
        if path is None:
            print(f"[.] {ts}  {msg.topic}  ({len(raw)} bytes, no mesh match)")
            return

        print(f"\n[+] Possible bed mesh found!")
        print(f"    topic : {msg.topic}")
        print(f"    field : {path}")
        print(f"    points: {len(flat_values)}")

        with open("cc2_mesh_raw.json", "w", encoding="utf-8") as f:
            json.dump({"topic": msg.topic, "field": path, "payload": payload},
                       f, indent=2)

        grid = to_grid(flat_values)
        np.savetxt("cc2_mesh.csv", grid, delimiter=",", fmt="%.4f")

        self.plot(grid)
        self.found = True
        print("\n[+] Saved cc2_mesh_raw.json, cc2_mesh.csv, cc2_mesh_heatmap.png")
        print("[+] You can keep the script running to catch more messages, "
              "or press Ctrl+C to stop.")

    def plot(self, grid):
        fig, ax = plt.subplots(figsize=(7, 6))
        im = ax.imshow(grid, cmap="coolwarm", origin="lower")
        for (i, j), val in np.ndenumerate(grid):
            ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                    fontsize=7, color="black")
        ax.set_title("Elegoo Centauri Carbon 2 - Bed Mesh (mm)")
        ax.set_xlabel("X probe index")
        ax.set_ylabel("Y probe index")
        fig.colorbar(im, ax=ax, label="Z offset (mm)")
        fig.tight_layout()
        fig.savefig("cc2_mesh_heatmap.png", dpi=150)
        plt.close(fig)

    def run(self):
        self.client.connect(self.args.host, self.args.port, keepalive=60)
        try:
            self.client.loop_forever()
        except KeyboardInterrupt:
            print("\n[+] Stopped by user.")
        finally:
            self.raw_log.close()


def main():
    parser = argparse.ArgumentParser(
        description="Capture and plot the CC2's bed mesh from its MQTT broker.")
    parser.add_argument("--host", required=True, help="Printer IP address")
    parser.add_argument("--port", type=int, default=1883, help="MQTT port (default 1883)")
    parser.add_argument("--user", default="elegoo", help="MQTT username (default 'elegoo')")
    parser.add_argument("--password", default="", help="MQTT password / LAN access code")
    parser.add_argument("--topic", default="#",
                         help="MQTT topic filter (default '#' = everything)")
    args = parser.parse_args()

    print("=" * 70)
    print(" CC2 bed mesh sniffer")
    print("=" * 70)
    print(f" Host   : {args.host}:{args.port}")
    print(f" Topic  : {args.topic}")
    print(" Waiting for connection...\n")

    sniffer = MeshSniffer(args)
    sniffer.run()


if __name__ == "__main__":
    main()
