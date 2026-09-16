# SEC 13F canonical storage: selective deterministic gzip

Date: 2026-09-17

## Decision

Adopt `lossless-gzip-v1-selective` for canonical `institutional_sponsorship/` snapshots. Compression is a storage representation only; SEC 13F methodology, identity mapping, PIT semantics, immutable snapshot layout, pointer-last publication, and two-distinct-date retention are unchanged.

The implementation reuses the proven CAN SLIM candidate pattern: gzip level 6, deterministic `mtime=0`, `.gz` object suffix, `Content-Encoding: gzip`, logical SHA-256 plus stored SHA-256/size in the manifest.

## Production audit

Audit run `35153295987` measured the canonical 2026-09-16 snapshot (`run-35076053291`) before implementation:

- original snapshot objects: 248,421,223 bytes
- hypothetical gzip-all: 239,924,092 bytes
- maximum bytes saved by gzip-all: 8,497,131 bytes (~3.42%)
- dominant `history/sponsorship_state_events.parquet`: 240,620,596 -> 237,877,433 bytes, only 1.14% saved

The dominant history object is already Parquet/internal-compressed, so whole-object gzip would add CPU and consumer complexity for negligible gain.

Large text artifacts compress strongly. Examples:

- `live/filings.csv`: 2,709,018 -> 482,410 bytes
- `live/sponsorship_by_cusip.csv`: 1,175,057 -> 257,433 bytes
- `live/sponsorship_mapped.csv`: 2,165,133 -> 412,718 bytes
- `live/amendment_lineage.csv`: 814,644 -> 137,193 bytes

## Backward-compatibility boundary

Existing pointer-exposed consumer artifacts remain in their legacy representation for this contract:

- history state/final Parquet
- uncertainty state/current Parquet
- live mapped CSV
- live amendment-lineage CSV
- live summary JSON

This prevents a storage-only change from silently breaking current CAN SLIM consumers that download pointer keys into fixed local file types.

Only non-pointer text artifacts are eligible for gzip when:

- extension is CSV/JSON/JSONL;
- logical size >= 1,024 bytes; and
- gzip stored/logical ratio <= 0.90.

On the audited canonical snapshot this selects `history/dataset_summaries.json`, `live/filings.csv`, and `live/sponsorship_by_cusip.csv`, predicting about 3,167,788 bytes saved per generation while preserving every existing pointer key.

## Integrity

Manifest schema remains v2 and now records `storage_contract`, storage summary, representation, logical hash/size, and stored hash/size where gzip is used. Compressed round-trip must equal the source bytes exactly. `current.json` remains the final write. Retention remains minimum two distinct snapshot dates and retains all existing pointer-protection checks.

## Lifecycle expectation

The first compressed generation coexists with the previous legacy generation by design, so namespace storage will not immediately reflect the full steady-state saving. Full steady-state saving appears after retention naturally contains two selectively compressed generations. Historical immutable snapshots are not rewritten or manually deleted.
