import csv
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'prepare_coverage_review.py'
spec = importlib.util.spec_from_file_location('coverage_review', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def create_input(path, domain='cyberjapandata.gsi.go.jp'):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=('layer', 'z', 'x', 'y', 'source_url'))
        writer.writeheader()
        for layer in module.LAYERS:
            for z, x, y in [(12, 100, 100), (18, 10000, 10000), (18, 10001, 10001)]:
                writer.writerow({'layer': layer, 'z': z, 'x': x, 'y': y,
                                 'source_url': f'https://{domain}/xyz/{layer}/{z}/{x}/{y}.png'})


def test_generate_offline_review(tmp_path):
    source = tmp_path / 'samples.csv'
    create_input(source)
    number, layers = module.write_review(source, tmp_path / 'out')
    assert number == 24
    assert layers == 8
    assert (tmp_path / 'out' / 'coverage_review.html').exists()
    assert 'network_requests_performed' in (tmp_path / 'out' / 'coverage_review_summary.json').read_text()
    with pytest.raises(FileExistsError):
        module.write_review(source, tmp_path / 'out')


def test_reject_other_domain(tmp_path):
    source = tmp_path / 'samples.csv'
    create_input(source, domain='example.org')
    with pytest.raises(ValueError, match='domain'):
        module.select_samples(source)
