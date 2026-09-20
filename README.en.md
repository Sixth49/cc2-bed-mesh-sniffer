   **🌐 Language:** [🇷🇺 Русский](README.md) | 🇬🇧 English
   **🌐 Language:** [🇷🇺 Русский](README.md) | 🇬🇧 English

# 🖨️ Elegoo Centauri Carbon 2 — Bed Mesh Sniffer & 3D Visualizer

**A toolkit for capturing the Elegoo CC2's MQTT traffic and building an interactive 3D bed height map**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)](https://www.python.org/) [![License](https://img.shields.io/badge/License-MIT-green)](https://github.com/Sixth49/cc2-bed-mesh-sniffer/blob/main/LICENSE) [![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](https://github.com/Sixth49/cc2-bed-mesh-sniffer/blob/main)

---

## 📖 About

The **Elegoo Centauri Carbon 2**'s stock interface doesn't expose a ready-made height map in a single message. During Auto Bed Leveling, the printer only streams incremental `gcode_move.x/y/z` coordinate updates over MQTT.

This project:

1. **Captures** all of the printer's MQTT traffic during calibration (and beyond).
2. **Reconstructs** the toolhead's trajectory from the coordinate deltas.
3. **Filters out** travel moves (retracts, parking) and keeps only the moments of contact with the bed.
4. **Recovers** the full height map (typically **11×11 = 121 points** on stock hardware).
5. **Builds** a 2D heatmap and an interactive 3D surface.

---

## ✨ Features

| Script | What it does |
| ------------------------------- | -------------------------------------------------------------------------------------------------- |
| 🔍 `cc2_mesh_sniffer.py` | Connects to the printer's built-in MQTT broker, logs all traffic and saves a raw dump |
| 📐 `cc2_bed_mesh_3d.py` | Analyzes the dump, auto-detects the calibration phase, filters out noise and builds an accurate height map |
| 🌐 Interactive 3D visualization | Generates an HTML file with a Plotly chart — rotate, zoom and pan right in the browser |
| 💾 Data export | Saves the grid to `.csv` and the heatmap to `.png` |

![3D bed map](https://github.com/user-attachments/assets/8b3d7acd-c96e-4687-9e49-6bff9de20797)
![Heatmap](https://github.com/user-attachments/assets/672589cc-d908-4b9f-91bd-0ef813c75655)

*Click an image to open it full-size*

---

## ⚙️ Requirements

- 🐍 **Python 3.8+** ([download](https://www.python.org/downloads/))
- 🌐 The printer and your PC must be on the **same local network**
- 📶 **LAN mode** must be enabled on the printer
`Settings → Network → LAN Only Mode → ON`

---

## 🚀 Quick start

### 1. Preparation

Download both scripts into the same folder:

- `cc2_mesh_sniffer.py`
- `cc2_bed_mesh_3d.py`

Open a terminal in that folder (on Windows: `Shift + Right-click` in the folder → *"Open in Terminal"*, or via `cd "path\to\folder"`).

### 2. Install dependencies

```
pip install paho-mqtt matplotlib numpy
```

### 3. Get the scripts

Save both files into the same folder:

- 🔍 `cc2_mesh_sniffer.py` — connects to the printer and logs all MQTT traffic to a file
- 📐 `cc2_bed_mesh_3d.py` — extracts the height map from that file and builds a 3D chart

### 4. Capture the traffic

Go to the folder with the scripts. On Windows: `Shift + Right-click` in the folder → *"Open in Terminal"*, or `cd "C:\Users\......`

Run the sniffer (fill in your printer's IP and access code):

```
python cc2_mesh_sniffer.py --host "PRINTER_IP" --password "ACCESS_CODE"
```

example: `python cc2_mesh_sniffer.py --host 192.168.1.50 --password 123456`

**Don't close the terminal!** While the script is running and printing messages to the console:

1. On the printer's screen, open **Settings → Auto Bed Leveling**
2. Start the calibration
3. Wait for it to **fully** finish
4. In the terminal window, press `Ctrl + C` to stop the script

A file named **`cc2_raw_dump.jsonl`** will appear in the scripts' folder — the raw log with all the calibration data.

---

### 5. Build the 3D bed map

In the same terminal, run:

```
python cc2_bed_mesh_3d.py cc2_raw_dump.jsonl
```

The script automatically finds the right calibration phase in the log and creates in the folder:

| File | Description |
| ---------------------------- | -------------------------------------------------------------------- |
| 🗺️ `mesh_heatmap.png` | 2D heatmap annotated with height values |
| 📊 `mesh_grid.csv` | The grid values as a table (handy for importing elsewhere) |
| 🌐 `mesh_3d_interactive.html` | Interactive 3D map — open it in a browser to rotate and zoom |

---

### 🎉 Done!

All the files with your bed's height map are now in the scripts' folder.

I suspect this same method could pull other data out of the printer too, but I haven't checked.
