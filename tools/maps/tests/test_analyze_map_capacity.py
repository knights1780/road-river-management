import csv
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_map_capacity.py"
spec = importlib.util.spec_from_file_location("analyze_map_capacity", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_report_with_realistic_fixture(tmp_path):
    review = tmp_path / "review.csv"
    with review.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["layer", "z", "x", "y", "availability"])
        writer.writeheader()
        for i, layer in enumerate(module.LAYERS):
            writer.writerow({"layer": layer, "z": "12", "x": str(i), "y": "1", "availability": "PRESENT"})
    raw = {"area_km2": 44.765, "total_tile_requests": 14023,
           "storage_mb_low": 493.9, "storage_mb_high": 1320.6,
           "layers": [{"layer": layer, "name": layer, "tile_count": i + 10, "low_mb": 1, "high_mb": 2}
                      for i, layer in enumerate(module.LAYERS)]}
    source = tmp_path / "tile_report.json"
    source.write_text(json.dumps(raw), encoding="utf-8")
    report = module.run(review, source, tmp_path / "out")
    assert report["total_present_samples"] == 8
    assert report["total_planned_tiles"] == 14023
    assert report["layers"][0]["measured_avg_bytes"] is None
    assert (tmp_path / "out" / "capacity_by_layer.csv").is_file()


def test_invalid_availability_rejected(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("layer,z,x,y,availability\nstd,1,0,0,SUCCESS\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid layer"):
        module.read_review(path)


def test_optional_sizes(tmp_path):
    path = tmp_path / "sizes.csv"
    path.write_text("layer,size_bytes\nstd,100\nstd,200\n", encoding="utf-8")
    assert module.optional_sizes(path)["std"] == [100, 200]
