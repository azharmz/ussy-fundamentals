from ussy_fundamentals.sec_13f_index import (
    acceptance_datetime_from_submission_text,
    filing_text_url,
    parse_master_index,
)


def test_parse_master_index_filters_13f_forms():
    text = """Description: Master Index of EDGAR Dissemination Feed\nCIK|Company Name|Form Type|Date Filed|Filename\n1067983|BERKSHIRE HATHAWAY INC|13F-HR|2026-05-15|edgar/data/1067983/0000950123-26-000001.txt\n123456|OTHER CO|10-Q|2026-05-15|edgar/data/123456/0000000000-26-000001.txt\n1067983|BERKSHIRE HATHAWAY INC|13F-HR/A|2026-05-20|edgar/data/1067983/0000950123-26-000002.txt\n"""
    df = parse_master_index(text)
    assert list(df["form_type"]) == ["13F-HR", "13F-HR/A"]
    assert list(df["accession_number"]) == ["0000950123-26-000001", "0000950123-26-000002"]
    assert list(df["accession_nodash"]) == ["000095012326000001", "000095012326000002"]


def test_acceptance_timestamp_from_submission_header():
    text = "<SEC-HEADER>\n<ACCEPTANCE-DATETIME>20260515164207\n</SEC-HEADER>"
    assert acceptance_datetime_from_submission_text(text) == "2026-05-15T16:42:07"
    assert acceptance_datetime_from_submission_text("<SEC-HEADER></SEC-HEADER>") is None


def test_filing_text_url_is_deterministic():
    assert filing_text_url(
        "0001067983",
        "000095012326000001",
        "edgar/data/1067983/0000950123-26-000001.txt",
    ) == "https://www.sec.gov/Archives/edgar/data/1067983/000095012326000001/0000950123-26-000001.txt"
