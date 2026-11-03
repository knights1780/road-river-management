"""Prepare a small human-reviewed GSI tile coverage checklist. No HTTP requests."""
from __future__ import annotations

import argparse
import csv
import html
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

LAYERS = (
    "std", "pale", "blank", "seamlessphoto", "lcmfc2",
    "lcm25k_2012", "swale", "relief",
)
HEADERS = (
    "layer", "z", "x", "y", "source_url", "availability",
    "checked_on", "image_or_error", "notes",
)


def select_samples(source: Path) -> list[dict[str, str]]:
    by_layer: dict[str, dict[int, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    with source.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"layer", "z", "x", "y", "source_url"}.issubset(reader.fieldnames or []):
            raise ValueError("review_samples.csv requires layer,z,x,y,source_url")
        for item in reader:
            layer = item["layer"]
            if layer not in LAYERS:
                raise ValueError(f"Unrecognized layer: {layer}")
            z, x, y = (int(item[k]) for k in ("z", "x", "y"))
            if z < 0 or z > 22 or x not in range(2**z) or y not in range(2**z):
                raise ValueError("Invalid XYZ coordinates")
            link = urlsplit(item["source_url"])
            if link.scheme != "https" or link.hostname != "cyberjapandata.gsi.go.jp":
                raise ValueError("Unexpected source domain")
            by_layer[layer][z].append(item)
    chosen = []
    for layer in LAYERS:
        zooms = sorted(by_layer[layer])
        if not zooms:
            raise ValueError(f"No candidate tile for {layer}")
        # Sample the highest zoom (3 points) and a middle zoom (1 point).
        z = zooms[-1]
        tiles = sorted(by_layer[layer][z], key=lambda r: (int(r["x"]), int(r["y"])))
        indices = sorted({0, len(tiles) // 2, len(tiles) - 1})
        selected = [tiles[i] for i in indices]
        if len(zooms) > 1:
            mid = zooms[(len(zooms) - 1) // 2]
            mid_tiles = by_layer[layer][mid]
            selected.append(mid_tiles[len(mid_tiles) // 2])
        for item in selected:
            chosen.append({
                "layer": item["layer"], "z": item["z"], "x": item["x"],
                "y": item["y"], "source_url": item["source_url"],
                "availability": "NOT_CHECKED", "checked_on": "",
                "image_or_error": "", "notes": "",
            })
    return chosen


def write_review(source: Path, destination: Path) -> tuple[int, int]:
    rows = select_samples(source)
    destination.mkdir(parents=True, exist_ok=True)
    csv_file = destination / "coverage_review.csv"
    # Explicitly prevent overwriting records after manual edits.
    if csv_file.exists():
        raise FileExistsError(f"Review file exists: {csv_file}; preserve manual observations")
    with csv_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    page = [
        "<!doctype html><html lang='ja'><meta charset='utf-8'>",
        "<title>吉田町 M2-C 確認候補</title>",
        "<style>body{font:15px system-ui,sans-serif;max-width:1000px;margin:2em auto;padding:0 1em}",
        "table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;text-align:left}",
        "tr:nth-child(even){background:#f5f5f5}</style>",
        "<h1>吉田町 M2-C 地図タイル確認候補</h1>",
        "<p>自動ダウンロードなし。下記リンクは手動で開く候補です。", 
        "リンク先の存在・利用許諾を保証しません。確認結果は coverage_review.csv に記録してください。</p>",
        "<p>記入例: availability = PRESENT / ABSENT / ERROR / NOT_CHECKED。</p>",
        "<table><thead><tr><th>レイヤー</th><th>Z/X/Y</th><th>確認候補URL</th></tr></thead><tbody>",
    ]
    for row in rows:
        label = html.escape(f"{row['layer']} {row['z']}/{row['x']}/{row['y']}")
        url = html.escape(row["source_url"], quote=True)
        page.append(f"<tr><td>{html.escape(row['layer'])}</td><td>{label}</td>"
                    f"<td><a href='{url}' target='_blank' rel='noopener noreferrer'>開いて確認</a></td></tr>")
    page.append("</tbody></table></html>")
    (destination / "coverage_review.html").write_text("\n".join(page), encoding="utf-8")
    (destination / "coverage_review_summary.json").write_text(
        json.dumps({"status": "AWAITING_MANUAL_VERIFICATION",
                    "network_requests_performed": 0, "sample_count": len(rows),
                    "per_layer": {layer: sum(r["layer"] == layer for r in rows) for layer in LAYERS},
                    "caution": "Samples cannot establish full-area coverage or license for offline storage."},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return len(rows), len(LAYERS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    n, layers = write_review(args.samples, args.output)
    print(f"Prepared {n} manual checks for {layers} layers; no network requests")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
