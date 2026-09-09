from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

LONG_NAME = "fundamentals_point_in_time_long.parquet"
WIDE_NAME = "fundamentals_point_in_time.parquet"
MANIFEST_NAME = "fundamentals_run_manifest.parquet"


def _norm_symbol(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "symbol" in out.columns:
        out["symbol"] = out["symbol"].astype(str).str.upper().str.strip()
    return out


def merge_incremental(
    *,
    baseline_dir: Path,
    delta_dir: Path | None,
    current_universe_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    current = pd.read_csv(current_universe_path, dtype=str)
    current["symbol"] = current["symbol"].astype(str).str.upper().str.strip()
    current_symbols = set(current["symbol"])

    base_long = _norm_symbol(pd.read_parquet(baseline_dir / LONG_NAME))
    base_wide = _norm_symbol(pd.read_parquet(baseline_dir / WIDE_NAME))
    base_manifest = _norm_symbol(pd.read_parquet(baseline_dir / MANIFEST_NAME))

    if delta_dir is not None and (delta_dir / MANIFEST_NAME).exists():
        delta_long = _norm_symbol(pd.read_parquet(delta_dir / LONG_NAME))
        delta_wide = _norm_symbol(pd.read_parquet(delta_dir / WIDE_NAME))
        delta_manifest = _norm_symbol(pd.read_parquet(delta_dir / MANIFEST_NAME))
        impacted = set(delta_manifest["symbol"])
    else:
        delta_long = pd.DataFrame(columns=base_long.columns)
        delta_wide = pd.DataFrame(columns=base_wide.columns)
        delta_manifest = pd.DataFrame(columns=base_manifest.columns)
        impacted = set()

    def combine(base: pd.DataFrame, delta: pd.DataFrame) -> pd.DataFrame:
        kept = base[base["symbol"].isin(current_symbols - impacted)].copy()
        if delta.empty:
            out = kept
        else:
            out = pd.concat([kept, delta[delta["symbol"].isin(current_symbols)]], ignore_index=True)
        return out

    long_df = combine(base_long, delta_long)
    wide_df = combine(base_wide, delta_wide)
    manifest = combine(base_manifest, delta_manifest)

    manifest_symbols = set(manifest["symbol"])
    missing = sorted(current_symbols - manifest_symbols)
    extra = sorted(manifest_symbols - current_symbols)
    if missing or extra:
        raise ValueError(f"Incremental merge does not reconcile to current universe: missing={missing[:20]}, extra={extra[:20]}")

    long_keys = [c for c in ["symbol", "metric", "accepted_at", "fiscal_period_end", "value", "accession"] if c in long_df.columns]
    if long_keys:
        long_df = long_df.drop_duplicates(subset=long_keys, keep="last")
    wide_keys = [c for c in ["symbol", "fiscal_period_end", "accepted_at", "accession"] if c in wide_df.columns]
    if wide_keys:
        wide_df = wide_df.drop_duplicates(subset=wide_keys, keep="last")
    manifest = manifest.drop_duplicates("symbol", keep="last")
    return long_df, wide_df, manifest


def main() -> None:
    p = argparse.ArgumentParser(description="Merge an incremental fundamentals delta into the current R2 baseline")
    p.add_argument("--baseline-dir", type=Path, default=Path("data/baseline"))
    p.add_argument("--delta-dir", type=Path, default=Path("data/delta"))
    p.add_argument("--current-universe", type=Path, default=Path("data/processed/current_universe.csv"))
    p.add_argument("--output-dir", type=Path, default=Path("data/processed"))
    args = p.parse_args()
    delta_dir = args.delta_dir if args.delta_dir.exists() else None
    long_df, wide_df, manifest = merge_incremental(
        baseline_dir=args.baseline_dir,
        delta_dir=delta_dir,
        current_universe_path=args.current_universe,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    long_df.to_parquet(args.output_dir / LONG_NAME, index=False)
    wide_df.to_parquet(args.output_dir / WIDE_NAME, index=False)
    manifest.to_parquet(args.output_dir / MANIFEST_NAME, index=False)
    print("=== INCREMENTAL MERGE ===")
    print(f"Current universe: {manifest['symbol'].nunique():,}")
    print(f"Normalized symbols: {wide_df['symbol'].nunique() if 'symbol' in wide_df.columns else 0:,}")
    print(f"Long rows: {len(long_df):,}")
    print(f"Wide rows: {len(wide_df):,}")


if __name__ == "__main__":
    main()
