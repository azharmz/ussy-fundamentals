from pathlib import Path

import requests

from ussy_fundamentals.sec_client import companyfacts


class MissingFactsClient:
    def get_json(self, url: str):
        response = requests.Response()
        response.status_code = 404
        response.url = url
        raise requests.HTTPError("404", response=response)


def test_companyfacts_404_returns_empty_without_cache(tmp_path: Path):
    cache = tmp_path / "companyfacts"
    result = companyfacts(MissingFactsClient(), "0000062362", cache)
    assert result == {}
    assert not (cache / "CIK0000062362.json").exists()
