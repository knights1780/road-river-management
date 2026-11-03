import csv
import gzip
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from audit_map_layers import inventory


def test_inventory(tmp_path):
    path = tmp_path / "index.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["layer", "z", "x", "y"])
        w.writerow(["std", 18, 230000, 100000])
        w.writerow(["seamlessphoto", 14, 14500, 6500])
    rows, samples = inventory(path)
    assert sum(r["tile_count"] for r in rows) == 2
    assert len(samples) == 2
    assert all(r["availability"] == "NOT_CHECKED" for r in samples)
