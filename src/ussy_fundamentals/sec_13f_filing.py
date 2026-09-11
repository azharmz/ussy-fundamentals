from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

ACCEPTANCE_RE = re.compile(r"<ACCEPTANCE-DATETIME>(\d{14})", re.I)
DOCUMENT_RE = re.compile(r"<DOCUMENT>(.*?)</DOCUMENT>", re.I | re.S)
TYPE_RE = re.compile(r"<TYPE>\s*([^\r\n<]+)", re.I)
TEXT_RE = re.compile(r"<TEXT>(.*)</TEXT>", re.I | re.S)


@dataclass(frozen=True)
class FilingMeta:
    accepted_at: str | None
    period_of_report: str | None
    is_amendment: bool | None
    amendment_type: str | None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(root: ET.Element, names: Iterable[str]) -> str | None:
    wanted = {n.lower() for n in names}
    for el in root.iter():
        if _local(el.tag) in wanted and el.text and el.text.strip():
            return el.text.strip()
    return None


def _xml_roots_from_submission(text: str) -> list[tuple[str, ET.Element]]:
    roots: list[tuple[str, ET.Element]] = []
    for block in DOCUMENT_RE.findall(text):
        typem = TYPE_RE.search(block)
        doctype = typem.group(1).strip() if typem else ""
        tm = TEXT_RE.search(block)
        payload = tm.group(1).strip() if tm else block.strip()
        start = payload.find("<?xml")
        if start < 0:
            for marker in ("<edgarSubmission", "<informationTable", "<form13FFileNumber"):
                p = payload.find(marker)
                if p >= 0:
                    start = p
                    break
        if start < 0:
            continue
        xml = payload[start:]
        try:
            roots.append((doctype, ET.fromstring(xml)))
        except ET.ParseError:
            continue
    return roots


def parse_filing_meta(text: str) -> FilingMeta:
    accepted = None
    m = ACCEPTANCE_RE.search(text)
    if m:
        accepted = pd.to_datetime(m.group(1), format="%Y%m%d%H%M%S").isoformat()

    period = None
    is_amendment: bool | None = None
    amendment_type = None
    for doctype, root in _xml_roots_from_submission(text):
        if doctype.upper().startswith("INFORMATION TABLE"):
            continue
        period = period or _first_text(root, ["periodOfReport"])
        raw_amend = _first_text(root, ["isAmendment"])
        if raw_amend is not None:
            is_amendment = raw_amend.strip().lower() in {"true", "1", "yes"}
        amendment_type = amendment_type or _first_text(root, ["amendmentType"])

    return FilingMeta(accepted, period, is_amendment, amendment_type)


def parse_information_table(text: str) -> pd.DataFrame:
    records: list[dict] = []
    for doctype, root in _xml_roots_from_submission(text):
        if not (doctype.upper().startswith("INFORMATION TABLE") or _local(root.tag) == "informationtable"):
            continue
        for info in root.iter():
            if _local(info.tag) != "infotable":
                continue
            row: dict[str, str | None] = {}
            for el in info.iter():
                name = _local(el.tag)
                value = el.text.strip() if el.text and el.text.strip() else None
                if name == "nameofissuer": row["issuer_name"] = value
                elif name == "titleofclass": row["title_of_class"] = value
                elif name == "cusip": row["cusip"] = value
                elif name == "figi": row["figi"] = value
                elif name == "value": row["value_thousands"] = value
                elif name == "sshprnamt": row["shares_or_principal"] = value
                elif name == "sshprnamttype": row["shares_type"] = value
                elif name == "putcall": row["put_call"] = value
                elif name == "investmentdiscretion": row["investment_discretion"] = value
                elif name == "othermanager": row["other_manager"] = value
                elif name == "sole": row["voting_sole"] = value
                elif name == "shared": row["voting_shared"] = value
                elif name == "none": row["voting_none"] = value
            if row.get("cusip"):
                records.append(row)

    cols = [
        "issuer_name", "title_of_class", "cusip", "figi", "value_thousands",
        "shares_or_principal", "shares_type", "put_call", "investment_discretion",
        "other_manager", "voting_sole", "voting_shared", "voting_none",
    ]
    df = pd.DataFrame(records, columns=cols)
    for col in ["value_thousands", "shares_or_principal", "voting_sole", "voting_shared", "voting_none"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "cusip" in df.columns:
        df["cusip"] = df["cusip"].astype("string").str.strip().str.upper()
    return df


def classify_amendment_state(meta: FilingMeta) -> str:
    if meta.is_amendment is False:
        return "BASE"
    if meta.is_amendment is True:
        t = (meta.amendment_type or "").strip().upper().replace(" ", "_")
        if "RESTATEMENT" in t:
            return "AMENDMENT_RESTATEMENT"
        if "NEW" in t and "HOLDING" in t:
            return "AMENDMENT_NEW_HOLDINGS"
        return "AMENDMENT_UNCLASSIFIED"
    return "AMENDMENT_STATE_UNKNOWN"
