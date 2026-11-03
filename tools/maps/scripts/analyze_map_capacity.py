"""Offline M2-D capacity report; never downloads tiles or changes reviewed CSV."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

LAYERS = ("std", "pale", "blank", "seamlessphoto", "lcmfc2", "lcm25k_2012", "swale", "relief")
VALID = {"PRESENT", "ABSENT", "ERROR", "NOT_CHECKED"}


def read_review(path: Path) -> tuple[list[dict[str, str]], dict[str, Counter]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"layer", "z", "x", "y", "availability"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"Missing CSV columns: {sorted(required - set(reader.fieldnames or []))}")
        rows = list(reader)
    seen = set()
    counts = defaultdict(Counter)
    for row in rows:
        layer = row["layer"].strip()
        status = row["availability"].strip().upper()
        if layer not in LAYERS or status not in VALID:
            raise ValueError(f"Invalid layer or availability: {layer}, {status}")
        key = tuple(row[field] for field in ("layer", "z", "x", "y"))
        if key in seen:
            raise ValueError(f"Duplicate sample: {key}")
        seen.add(key)
        counts[layer][status] += 1
    return rows, counts


def optional_sizes(path: Path | None) -> dict[str, list[int]]:
    """Optional CSV: layer,size_bytes; values must be independently measured."""
    sizes = defaultdict(list)
    if path is None:
        return sizes
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"layer", "size_bytes"}.issubset(reader.fieldnames or []):
            raise ValueError("Sizes CSV must have layer,size_bytes")
        for row in reader:
            layer = row["layer"].strip()
            size = int(row["size_bytes"])
            if layer not in LAYERS or size < 1:
                raise ValueError("Invalid layer or size_bytes")
            sizes[layer].append(size)
    return sizes


def build_report(tile_report: dict, counts: dict[str, Counter], sizes: dict[str, list[int]]) -> dict:
    layers = []
    for layer in tile_report["layers"]:
        code = layer["layer"]
        observed = counts[code]
        measured = sizes[code]
        item = {
            "layer": code,
            "name": layer["name"],
            "planned_tiles": layer["tile_count"],
            "checked_present": observed["PRESENT"],
            "checked_absent": observed["ABSENT"],
            "checked_error": observed["ERROR"],
            "not_checked": observed["NOT_CHECKED"],
            "estimated_low_mib": layer["low_mb"],
            "estimated_high_mib": layer["high_mb"],
            "measured_samples": len(measured),
            "measured_avg_bytes": round(sum(measured) / len(measured)) if measured else None,
            "sample_extrapolation_mib": round(layer["tile_count"] * sum(measured) / len(measured) / (1024**2), 2) if measured else None,
        }
        layers.append(item)
    return {
        "status": "ESTIMATE_ONLY",
        "area_km2": tile_report["area_km2"],
        "total_planned_tiles": tile_report["total_tile_requests"],
        "total_reviewed_samples": sum(sum(c.values()) for c in counts.values()),
        "total_present_samples": sum(c["PRESENT"] for c in counts.values()),
        "estimate_low_mib": tile_report["storage_mb_low"],
        "estimate_high_mib": tile_report["storage_mb_high"],
        "layers": layers,
        "limitations": [
            "Sample availability is not full-area coverage.",
            "Estimated ranges are assumptions, not downloaded bytes.",
            "Optional measured sizes are not necessarily representative across zooms and areas.",
            "No tiles downloaded; NAS storage and mass download permission remain unverified.",
        ],
    }


def run(review: Path, tile_report: Path, output: Path, sizes_csv: Path | None = None) -> dict:
    _, counts = read_review(review)
    source = json.loads(tile_report.read_text(encoding="utf-8-sig"))
    if {item["layer"] for item in source["layers"]} != set(LAYERS):
        raise ValueError("Tile report must contain exactly the eight approved layers")
    report = build_report(source, counts, optional_sizes(sizes_csv))
    output.mkdir(parents=True, exist_ok=True)
    (output / "capacity_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = list(report["layers"][0])
    with (output / "capacity_by_layer.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["layers"])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--tile-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sizes-csv", type=Path)
    args = parser.parse_args()
    result = run(args.review, args.tile_report, args.output, args.sizes_csv)
    print(f"Samples PRESENT: {result['total_present_samples']}/{result['total_reviewed_samples']}")
    print(f"Planned tiles: {result['total_planned_tiles']:,}; estimate: {result['estimate_low_mib']}-{result['estimate_high_mib']} MiB")
    print(f"Report: {args.output / 'capacity_summary.json'}")
    print("No network access, no tile download.")


if __name__ == "__main__":
    main()
