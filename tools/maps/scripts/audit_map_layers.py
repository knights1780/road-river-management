"""Offline M2 inventory for Yoshida Town map tile planning.

Reads the previously generated tile_index.csv.gz. Never makes network requests.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

LAYERS = {
    "std": ("標準地図", 5, 18, "png", "全国"),
    "pale": ("淡色地図", 5, 18, "png", "全国"),
    "blank": ("白地図", 5, 14, "png", "全国"),
    "seamlessphoto": ("最新航空写真", 14, 18, "jpg", "地域・解像度要確認"),
    "lcmfc2": ("治水地形分類図", 11, 16, "png", "地域要確認"),
    "lcm25k_2012": ("土地条件図", 10, 16, "png", "地域・識別子要確認"),
    "swale": ("明治期の低湿地", 10, 16, "png", "地域要確認"),
    "relief": ("色別標高図", 5, 15, "png", "全国"),
}


def inventory(path: Path) -> tuple[list[dict[str, str | int]], list[dict[str, str | int]]]:
    counts: Counter[tuple[str, int]] = Counter()
    samples: dict[tuple[str, int], list[tuple[int, int]]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not {"layer", "z", "x", "y"}.issubset(reader.fieldnames or []):
            raise ValueError("tile_index.csv.gz requires layer,z,x,y columns")
        for item in reader:
            layer = item["layer"]
            if layer not in LAYERS:
                raise ValueError(f"Unknown layer: {layer}")
            z, x, y = (int(item[k]) for k in ("z", "x", "y"))
            name, zmin, zmax, _, _ = LAYERS[layer]
            if not zmin <= z <= zmax or not (0 <= x < 2**z and 0 <= y < 2**z):
                raise ValueError(f"Invalid tile {layer}/{z}/{x}/{y}")
            key = (layer, z)
            counts[key] += 1
            # Deterministic first/central/last candidate for each zoom.
            if len(samples[key]) < 3:
                samples[key].append((x, y))
            else:
                samples[key][1] = (x, y)
    summary = []
    candidates = []
    for layer, (name, zmin, zmax, ext, region) in LAYERS.items():
        total = sum(counts[layer, z] for z in range(zmin, zmax + 1))
        summary.append({"layer": layer, "name": name, "z_min": zmin, "z_max": zmax,
                        "tile_count": total, "coverage": region, "verification": "NOT_CHECKED"})
        for z in range(zmin, zmax + 1):
            for x, y in samples.get((layer, z), []):
                candidates.append({"layer": layer, "z": z, "x": x, "y": y,
                                   "source_url": f"https://cyberjapandata.gsi.go.jp/xyz/{layer}/{z}/{x}/{y}.{ext}",
                                   "availability": "NOT_CHECKED"})
    return summary, candidates


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline map layer audit; no downloads")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    rows, samples = inventory(args.index)
    write_csv(args.output / "layer_inventory.csv", rows)
    write_csv(args.output / "review_samples.csv", samples)
    (args.output / "m2_summary.json").write_text(
        json.dumps({"status": "AWAITING_COVERAGE_AND_USAGE_CONFIRMATION",
                    "tile_count": sum(int(r["tile_count"]) for r in rows),
                    "layers": rows,
                    "note": "Planned requests, not confirmed available tiles. No download performed."},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Created offline inventory. Planned tiles:", sum(int(r["tile_count"]) for r in rows))
    print("Sample candidates:", len(samples))
    print("Output:", args.output)


if __name__ == "__main__":
    main()
