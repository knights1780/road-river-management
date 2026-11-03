#!/usr/bin/env python3
"""Clip surrounding regions to a hand-drawn polygon and recalculate tiles.

Never removes Yoshida municipality or its 1-km coastal buffer.
Uses original M1 tile counting logic; no network or download operations.
"""
import argparse
import importlib.util
import json
from pathlib import Path
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

REQUIRED_ZONES = {'yoshida', 'coastal_sea'}


def geometry_from_geojson(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    if document.get('type') == 'FeatureCollection':
        geoms = [shape(f['geometry']) for f in document['features'] if f.get('geometry')]
    elif document.get('type') == 'Feature':
        geoms = [shape(document['geometry'])]
    else:
        geoms = [shape(document)]
    if not geoms:
        raise ValueError(f'No polygon geometry in {path}')
    polygons = []
    for geom in geoms:
        if not geom.is_valid:
            geom = make_valid(geom)
        if geom.geom_type not in ('Polygon', 'MultiPolygon'):
            raise ValueError('Draw polygon(s), not points or lines')
        polygons.append(geom)
    return unary_union(polygons)


def clip_zones(zones_doc, selection):
    features = []
    ids = set()
    before = 0.0
    for feat in zones_doc['features']:
        props = feat['properties']
        zone_id = props['zone_id']
        ids.add(zone_id)
        geom = shape(feat['geometry'])
        before += geom.area
        if zone_id not in REQUIRED_ZONES:
            geom = geom.intersection(selection)
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = make_valid(geom)
        # GeometryCollections may contain lines on polygon boundaries.
        if geom.geom_type == 'GeometryCollection':
            components = [g for g in geom.geoms if g.geom_type in ('Polygon', 'MultiPolygon')]
            if not components:
                continue
            geom = unary_union(components)
        if geom.area <= 0:
            continue
        features.append({'type': 'Feature', 'properties': dict(props, trim_status='protected' if zone_id in REQUIRED_ZONES else 'clipped'), 'geometry': geom.__geo_interface__})
    missing = REQUIRED_ZONES - ids
    if missing:
        raise ValueError(f'Missing required base zones: {sorted(missing)}')
    combined = unary_union([shape(f['geometry']) for f in features])
    return combined, features


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--zones', required=True, type=Path)
    p.add_argument('--selection', required=True, type=Path)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--base-script', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    selection = geometry_from_geojson(a.selection)
    zones_doc = json.loads(a.zones.read_text(encoding='utf-8'))
    area, features = clip_zones(zones_doc, selection)
    if not area.is_valid or area.is_empty:
        raise ValueError('Invalid result geometry')
    spec = importlib.util.spec_from_file_location('rrm_m1_service_area', a.base_script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads(a.config.read_text(encoding='utf-8'))
    output = a.output
    if output.resolve() == a.zones.parent.resolve():
        raise ValueError('Use a NEW output directory to avoid overwriting original staging results')
    report = module.build_outputs(area, features, {}, config, output, True)
    report['source_selection'] = str(a.selection.resolve())
    report['status'] = 'DRAFT_REVIEW_REQUIRED'
    (output / 'tile_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"New area: {report['area_km2']:.3f} km²; total tiles: {report['total_tile_requests']:,}")
    print(f"Storage: {report['storage_mb_low']:.1f}–{report['storage_mb_high']:.1f} MiB")
    print('Output:', output.resolve())
    print('Protected zones: yoshida, coastal_sea. Still DRAFT until visually verified.')


if __name__ == '__main__':
    main()
