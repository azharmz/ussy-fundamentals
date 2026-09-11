from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from .sec_client import SEC_WWW, SecClient

CATALOG_URL = f'{SEC_WWW}/data-research/sec-markets-data/form-13f-data-sets'
ANCHOR_RE = re.compile(r'<a\b[^>]*href=["\']([^"\']+\.zip)["\'][^>]*>(.*?)</a>', re.I | re.S)
TAG_RE = re.compile(r'<[^>]+>')


def discover() -> list[dict]:
    text = SecClient().get_text(CATALOG_URL)
    rows = []
    seen = set()
    for href, label_html in ANCHOR_RE.findall(text):
        url = urljoin(CATALOG_URL, html.unescape(href))
        if 'form-13f' not in url.lower() and 'form13f' not in url.lower():
            continue
        if url in seen:
            continue
        seen.add(url)
        label = html.unescape(TAG_RE.sub(' ', label_html))
        label = ' '.join(label.split())
        rows.append({'label': label, 'url': url})
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, default=Path('data/13f/bulk_catalog.json'))
    a = p.parse_args()
    rows = discover()
    if not rows:
        raise RuntimeError('No official 13F ZIP links discovered')
    a.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'catalog_url': CATALOG_URL,
        'dataset_count': len(rows),
        'datasets': rows,
        'strategy_returns_inspected': False,
    }
    a.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
