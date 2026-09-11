from ussy_fundamentals.sec_13f_filing import (
    classify_amendment_state,
    parse_filing_meta,
    parse_information_table,
)

SAMPLE = '''<SEC-DOCUMENT>
<ACCEPTANCE-DATETIME>20260515123045
<DOCUMENT>
<TYPE>13F-HR
<TEXT><?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler">
  <formData>
    <coverPage><reportCalendarOrQuarter>03-31-2026</reportCalendarOrQuarter></coverPage>
    <signatureBlock><name>Tester</name></signatureBlock>
    <summaryPage><otherIncludedManagersCount>0</otherIncludedManagersCount></summaryPage>
  </formData>
  <headerData><submissionType>13F-HR</submissionType><filerInfo><periodOfReport>03-31-2026</periodOfReport></filerInfo></headerData>
</edgarSubmission>
</TEXT></DOCUMENT>
<DOCUMENT>
<TYPE>INFORMATION TABLE
<TEXT><?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ACME INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>123456789</cusip>
    <value>1000</value><shrsOrPrnAmt><sshPrnamt>25000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion><votingAuthority><Sole>25000</Sole><Shared>0</Shared><None>0</None></votingAuthority>
  </infoTable>
</informationTable>
</TEXT></DOCUMENT>
</SEC-DOCUMENT>'''

AMENDED = SAMPLE.replace('<submissionType>13F-HR</submissionType>', '<submissionType>13F-HR/A</submissionType><isAmendment>true</isAmendment><amendmentType>RESTATEMENT</amendmentType>')


def test_parse_base_filing():
    meta = parse_filing_meta(SAMPLE)
    assert meta.accepted_at.startswith('2026-05-15T12:30:45')
    assert meta.period_of_report == '03-31-2026'
    assert classify_amendment_state(meta) in {'BASE', 'AMENDMENT_STATE_UNKNOWN'}
    df = parse_information_table(SAMPLE)
    assert len(df) == 1
    assert df.iloc[0]['cusip'] == '123456789'
    assert df.iloc[0]['shares_or_principal'] == 25000


def test_parse_restatement():
    meta = parse_filing_meta(AMENDED)
    assert meta.is_amendment is True
    assert classify_amendment_state(meta) == 'AMENDMENT_RESTATEMENT'
