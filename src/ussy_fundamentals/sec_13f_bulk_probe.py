from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from io import BytesIO
from pathlib import Path

from .sec_client import SecClient

DEFAULT_URL = 'https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01mar2026-31may2026_form13f.zip'


def run(url: str, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    client = SecClient()
    raw = client.get_bytes(url)
    sha = hashlib.sha256(raw).hexdigest()
    if not zipfile.is_zipfile(BytesIO(raw)):
        raise RuntimeError('SEC response is not a ZIP archive')
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        names = zf.namelist()
        listing = []
        for info in zf.infolist():
            listing.append({'name': info.filename, 'size': info.file_size})
    summary = {
        'url': url,
        'size_bytes': len(raw),
        'sha256': sha,
        'zip_valid': True,
        'file_count': len(names),
        'files': listing,
        'strategy_returns_inspected': False,
    }
    (output / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--url', default=DEFAULT_URL)
    p.add_argument('--output', type=Path, default=Path('data/13f/bulk_probe'))
    a = p.parse_args()
    run(a.url, a.output)


if __name__ == '__main__':
    main()
