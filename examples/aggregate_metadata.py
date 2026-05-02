"""Demo: aggregate per-ingest metadata.yaml files into a unified report.

This is what monarch-ingest (or any consumer building a merged KG) would do
to collect provenance across many kozahub ingests. Each ingest produces an
`output/metadata.yaml`; this script gathers them, deduplicates upstream
sources by INFORES id, and emits a flat report — flagging any cross-ingest
version disagreements.

Usage:
    python aggregate_metadata.py path/to/ingest1 path/to/ingest2 ...
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml


def load_metadata(ingest_dir: Path) -> dict | None:
    p = ingest_dir / "output" / "release-metadata.yaml"
    if not p.is_file():
        print(f"  [skip] {ingest_dir.name}: no output/release-metadata.yaml", file=sys.stderr)
        return None
    return yaml.safe_load(p.read_text())


def aggregate(ingest_dirs: list[Path]) -> dict:
    by_source: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    builds = []

    for d in ingest_dirs:
        m = load_metadata(d)
        if m is None:
            continue
        builds.append({
            "ingest": m["source"],
            "build_version": m["build_version"],
            "transform_version": m["transform_version"],
            "biolink_version": m.get("biolink_version"),
            "generated_at": m.get("generated_at"),
            "artifact_count": len(m.get("artifacts") or []),
            "url_count": sum(len(s.get("urls") or []) for s in m.get("sources") or []),
        })
        for src in m.get("sources") or []:
            by_source[src["id"]].append((m["source"], src))

    # Detect version disagreements when the same source is consumed by multiple ingests.
    consolidated_sources = []
    disagreements = []
    for source_id, observations in sorted(by_source.items()):
        versions = {obs["version"] for _, obs in observations}
        ingests = sorted({ingest for ingest, _ in observations})
        rep = observations[0][1]
        consolidated_sources.append({
            "id": source_id,
            "name": rep.get("name"),
            "version": rep["version"] if len(versions) == 1 else f"DISAGREE: {sorted(versions)}",
            "consumed_by": ingests,
        })
        if len(versions) > 1:
            disagreements.append({
                "id": source_id,
                "versions_observed": sorted(versions),
                "by_ingest": {ingest: obs["version"] for ingest, obs in observations},
            })

    return {
        "builds": builds,
        "sources": consolidated_sources,
        "disagreements": disagreements,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    ingest_dirs = [Path(a).resolve() for a in sys.argv[1:]]
    report = aggregate(ingest_dirs)

    print("=" * 60)
    print("Build receipts")
    print("=" * 60)
    for b in report["builds"]:
        print(f"  {b['ingest']:20s}  {b['build_version']}")
        print(f"  {'':20s}  {b['url_count']} url(s) ingested, {b['artifact_count']} artifact(s) produced @ {b['generated_at']}")

    print()
    print("=" * 60)
    print("Upstream sources (deduplicated across ingests)")
    print("=" * 60)
    for s in report["sources"]:
        consumed = ", ".join(s["consumed_by"])
        print(f"  {s['id']:25s} v{s['version']:15s} ← {consumed}")

    if report["disagreements"]:
        print()
        print("=" * 60)
        print("⚠  Version disagreements")
        print("=" * 60)
        for d in report["disagreements"]:
            print(f"  {d['id']}:")
            for ingest, ver in d["by_ingest"].items():
                print(f"    {ingest}: {ver}")

    # Also dump a YAML-shaped consolidated report for machine consumers.
    out = Path("aggregated-metadata.yaml")
    out.write_text(yaml.safe_dump(report, sort_keys=False, default_flow_style=False))
    print(f"\nFull report: {out.resolve()}")
