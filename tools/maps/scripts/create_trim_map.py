#!/usr/bin/env python3
"""Create a browser-based polygon selection page (no map tile requests)."""
import argparse
from pathlib import Path
import folium
from folium.plugins import Draw
import geopandas as gpd


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--zones', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    zones = gpd.read_file(a.zones).to_crs('EPSG:4326')
    m = folium.Map(tiles=None, zoom_control=True, control_scale=True)
    palette = {'yoshida': '#16a34a', 'coastal_sea': '#0891b2', 'hatsukura': '#e8790c', 'oigawa': '#8b5cf6', 'haibara_east': '#db2777'}
    folium.GeoJson(
        zones,
        name='現在の地区区域',
        style_function=lambda f: {'color': palette.get(f['properties'].get('zone_id'), '#2563eb'), 'weight': 2, 'fillOpacity': .14},
        tooltip=folium.GeoJsonTooltip(fields=['name', 'zone_id']),
    ).add_to(m)
    Draw(
        export=True,
        filename='trim_selection.geojson',
        position='topleft',
        draw_options={'polyline': False, 'rectangle': False, 'circle': False, 'marker': False, 'circlemarker': False, 'polygon': {'allowIntersection': False, 'showArea': True}},
        edit_options={'edit': True, 'remove': True},
    ).add_to(m)
    folium.LayerControl().add_to(m)
    b = zones.total_bounds
    m.fit_bounds([[b[1], b[0]], [b[3], b[2]]])
    a.output.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(a.output))
    print(f'Preview: {a.output}\nDraw one polygon around the area to retain, then click Export on the top-left toolbar.')
    print('This HTML uses external JS/CSS CDN resources, but downloads NO map tiles.')


if __name__ == '__main__':
    main()
