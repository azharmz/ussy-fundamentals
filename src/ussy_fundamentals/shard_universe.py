from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def shard_universe(universe: pd.DataFrame, shard_index: int, shard_count: int) -> pd.DataFrame:
    """Return a deterministic round-robin shard of a canonical universe.

    Round-robin partitioning spreads adjacent securities across workers and keeps every
    requested row in exactly one shard without re-sorting away the canonical order.
    """
    if shard_count <= 0:
        raise ValueError("shard_count must be > 0")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError(f"shard_index must be in [0, {shard_count})")
    if universe.empty:
        return universe.copy()

    positions = pd.Series(range(len(universe)), index=universe.index)
    return universe.loc[(positions % shard_count) == shard_index].copy().reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create one deterministic shard from a canonical universe CSV")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--shard-index", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    args = parser.parse_args()

    universe = pd.read_csv(args.input)
    if "symbol" not in universe.columns:
        raise SystemExit(f"Universe has no symbol column: {args.input}")

    shard = shard_universe(universe, args.shard_index, args.shard_count)
    if shard.empty:
        raise SystemExit(
            f"Shard {args.shard_index}/{args.shard_count} is empty for universe size {len(universe)}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shard.to_csv(args.output, index=False)
    print(
        f"Shard {args.shard_index + 1}/{args.shard_count}: "
        f"{len(shard):,} of {len(universe):,} requested securities"
    )
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
