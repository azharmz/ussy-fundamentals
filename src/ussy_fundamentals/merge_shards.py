from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


LONG_NAME = "fundamentals_point_in_time_long.parquet"
WIDE_NAME = "fundamentals_point_in_time.parquet"
MANIFEST_NAME = "fundamentals_run_manifest.parquet"


def _read_many(paths: list[Path]) -> pd.DataFrame:
    frames = [pd.read_parquet(path) for path in paths]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def merge_shard_outputs(root: Path, expected_universe: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    long_paths = sorted(root.rglob(LONG_NAME))
    wide_paths = sorted(root.rglob(WIDE_NAME))
    manifest_paths = sorted(root.rglob(MANIFEST_NAME))
    if not long_paths or not wide_paths or not manifest_paths:
        raise ValueError(
            f"Missing shard outputs under {root}: "
            f"long={len(long_paths)}, wide={len(wide_paths)}, manifest={len(manifest_paths)}"
        )
    if not (len(long_paths) == len(wide_paths) == len(manifest_paths)):
        raise ValueError(
            f"Shard artifact counts disagree: long={len(long_paths)}, "
            f"wide={len(wide_paths)}, manifest={len(manifest_paths)}"
        )

    long_df = _read_many(long_paths)
    wide_df = _read_many(wide_paths)
    manifest = _read_many(manifest_paths)

    if "symbol" not in manifest.columns:
        raise ValueError("Merged run manifest has no symbol column")
    manifest["symbol"] = manifest["symbol"].astype(str).str.upper().str.strip()
    dupes = manifest[manifest["symbol"].duplicated(keep=False)]["symbol"].unique().tolist()
    if dupes:
        raise ValueError(f"Symbols appear in more than one shard manifest: {sorted(dupes)[:20]}")

    if expected_universe is not None:
        expected = pd.read_csv(expected_universe)
        if "symbol" not in expected.columns:
            raise ValueError(f"Expected universe has no symbol column: {expected_universe}")
        expected_symbols = set(expected["symbol"].astype(str).str.upper().str.strip())
        manifest_symbols = set(manifest["symbol"])
        missing = sorted(expected_symbols - manifest_symbols)
        extra = sorted(manifest_symbols - expected_symbols)
        if missing or extra:
            raise ValueError(
                f"Merged manifest does not reconcile to canonical universe: "
                f"missing={missing[:20]}, extra={extra[:20]}"
            )

    long_keys = [
        c for c in ["symbol", "metric", "accepted_at", "fiscal_period_end", "value", "accession"]
        if c in long_df.columns
    ]
    if long_keys:
        long_df = long_df.drop_duplicates(subset=long_keys, keep="last")

    wide_keys = [
        c for c in ["symbol", "fiscal_period_end", "accepted_at", "accession"]
        if c in wide_df.columns
    ]
    if wide_keys:
        wide_df = wide_df.drop_duplicates(subset=wide_keys, keep="last")

    return long_df, wide_df, manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge fundamentals shard artifacts into canonical processed outputs")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--expected-universe", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    args = parser.parse_args()

    long_df, wide_df, manifest = merge_shard_outputs(args.root, args.expected_universe)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    long_path = args.output_dir / LONG_NAME
    wide_path = args.output_dir / WIDE_NAME
    manifest_path = args.output_dir / MANIFEST_NAME
    long_df.to_parquet(long_path, index=False)
    wide_df.to_parquet(wide_path, index=False)
    manifest.to_parquet(manifest_path, index=False)

    print("=== SHARD MERGE ===")
    print(f"Requested symbols: {manifest['symbol'].nunique():,}")
    print(f"Normalized symbols: {wide_df['symbol'].nunique() if 'symbol' in wide_df.columns else 0:,}")
    print(f"Wide rows: {len(wide_df):,}")
    print(f"Long rows: {len(long_df):,}")
    print(f"Wrote {long_path}")
    print(f"Wrote {wide_path}")
    print(f"Wrote {manifest_path}")


if __name__ == "__main__":
    main()
