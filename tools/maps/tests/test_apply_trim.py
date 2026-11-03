from shapely.geometry import box, mapping, shape
from scripts.apply_trim import clip_zones


def test_protected_geometries_stay_whole():
    doc = {'features': [
        {'properties': {'zone_id': k}, 'geometry': mapping(g)}
        for k, g in [
            ('yoshida', box(0, 0, 2, 2)),
            ('coastal_sea', box(0, -1, 2, 0)),
            ('hatsukura', box(-3, 0, 0, 2)),
        ]
    ]}
    whole, feats = clip_zones(doc, box(-1, 0, 1, 2))
    d = {f['properties']['zone_id']: shape(f['geometry']) for f in feats}
    assert d['yoshida'].equals(box(0, 0, 2, 2))
    assert d['coastal_sea'].equals(box(0, -1, 2, 0))
    assert d['hatsukura'].equals(box(-1, 0, 0, 2))
    assert whole.area == 8
