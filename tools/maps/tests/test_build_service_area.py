import importlib.util
import json
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aoi", HERE / "scripts/build_service_area.py")
aoi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aoi)


def test_tile_roundtrip_and_one_tile():
    x, y = aoi._lon_x(138.24, 18), aoi._lat_y(34.77, 18)
    square = aoi._tile_bounds(18, x, y)
    assert (18, x, y) in list(aoi.enumerate_tiles(square.buffer(-0.00001), 18))


def test_create_area_with_public_data_like_fixture(tmp_path):
    # Synthetic polygons are solely for deterministic code tests, not geographic evidence.
    municipalities = gpd.GeoDataFrame(
        {"N03_004": ["吉田町", "島田市", "焼津市", "牧之原市"],
         "geometry": [box(138.2, 34.7, 138.3, 34.78), box(138.2, 34.78, 138.3, 34.9),
                      box(138.3, 34.70, 138.4, 34.85), box(138.1, 34.70, 138.2, 34.85)]},
        crs="EPSG:4326")
    districts = gpd.GeoDataFrame(
        {"CITY_NAME": ["島田市", "焼津市", "牧之原市", "島田市"],
         "S_NAME": ["阪本", "宗高", "細江", "野田"],
         "geometry": [box(138.22, 34.80, 138.26, 34.85), box(138.32, 34.75, 138.35, 34.79),
                      box(138.15, 34.72, 138.18, 34.77), box(138.21, 34.84, 138.25, 34.87)]},
        crs="EPSG:4326")
    config = {"base_municipality": {"name": "吉田町"}, "sea_buffer_m": 1000,
              "projection_for_buffer": "EPSG:32654", "municipality_source": "test", "small_area_source": "test",
              "districts": [{"id": "h", "municipality": "島田市", "name": "初倉", "name_prefixes": ["阪本"]},
                            {"id": "o", "municipality": "焼津市", "name": "大井川", "name_prefixes": ["宗高"]},
                            {"id": "e", "municipality": "牧之原市", "name": "榛原", "name_prefixes": ["細江"]}],
              "tile_layers": [{"id": "std", "name": "標準", "min_zoom": 10, "max_zoom": 11, "average_kb_low": 20,
                               "average_kb_high": 60}]}
    geom, features, audits = aoi.create_area(municipalities, districts, config)
    assert geom.contains(districts.geometry.iloc[0].centroid)
    assert geom.contains(districts.geometry.iloc[1].centroid)
    assert geom.contains(districts.geometry.iloc[2].centroid)
    assert not geom.contains(districts.geometry.iloc[3].centroid)
    assert [f["properties"]["zone_id"] for f in features][:4] == ["yoshida", "h", "o", "e"]
    assert audits["h"]["matched_names"] == ["阪本"]
    report = aoi.build_outputs(geom, features, audits, config, tmp_path, True)
    assert report["total_tile_requests"] > 0
    assert (tmp_path / "service_area.geojson").exists()
    assert (tmp_path / "tile_index.csv.gz").exists()
    assert json.loads((tmp_path / "service_area.geojson").read_text())["type"] == "FeatureCollection"
