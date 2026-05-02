"""Compose and write ReleaseMetadata YAML for a kozahub ingest run.

Each ingest provides a `get_source_versions()` function that returns a list of
SourceVersion-shaped dicts. This module wraps that with the boilerplate:
content-hashing the transform code, looking up tool versions, computing the
build_version composite key, and emitting `output/release-metadata.yaml`.

Also exports `urls_from_download_yaml()` plus a small set of version-fetcher
helpers covering the patterns common across kozahub ingests, so individual
versions.py modules stay short.
"""

from __future__ import annotations

import datetime
import email.utils
import hashlib
import importlib.metadata
import re
from pathlib import Path
from typing import Iterable

import yaml


SCHEMA_PATH = Path(__file__).parent / "schema" / "kozahub_metadata_schema.yaml"


def _now_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _content_hash(paths: Iterable[Path]) -> str:
    hasher = hashlib.sha256()
    for p in sorted(paths):
        if p.is_file():
            hasher.update(p.read_bytes())
    return hasher.hexdigest()[:8]


def _try_pkg_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _file_sha256(p: Path) -> str | None:
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_metadata(
    ingest_name: str,
    source_versions: list[dict],
    transform_paths: Iterable[Path],
    artifacts: Iterable[str] | None = None,
    output_dir: Path = Path("output"),
    release_version: str | None = None,
    primary_source_version: str | None = None,
) -> dict:
    """Compose IngestMetadata, write output_dir/metadata.yaml, return the dict.

    transform_paths: files whose content goes into transform_version (e.g.
        the ingest's *.py and ingest yaml under src/).
    artifacts: artifact paths relative to output_dir; sha256 is computed if
        the file exists.
    """
    transform_paths = list(transform_paths)
    transform_version = _content_hash(transform_paths)

    biolink_version = _try_pkg_version("biolink-model") or "unknown"

    if primary_source_version is None and source_versions:
        primary_source_version = source_versions[0].get("version", "unknown")

    build_version = "_".join(
        x for x in [
            ingest_name,
            primary_source_version,
            transform_version,
            biolink_version,
        ] if x
    )

    output_dir = Path(output_dir)
    artifact_records = []
    for path in (artifacts or []):
        full = output_dir / path
        artifact_records.append({
            "path": path,
            **({"sha256": _file_sha256(full)} if _file_sha256(full) else {}),
        })

    tools = {}
    for pkg, key in [
        ("koza", "koza"),
        ("biolink-model", "biolink_model"),
        ("kghub-downloader", "kghub_downloader"),
    ]:
        v = _try_pkg_version(pkg)
        if v:
            tools[key] = v

    metadata = {
        "source": ingest_name,
        "source_version": primary_source_version,
        "transform_version": transform_version,
        "biolink_version": biolink_version,
        "build_version": build_version,
        "generated_at": _now_iso(),
        "sources": source_versions,
    }
    if release_version:
        metadata["release_version"] = release_version
    if artifact_records:
        metadata["artifacts"] = artifact_records
    if tools:
        metadata["tools"] = tools

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "release-metadata.yaml"
    with out_path.open("w") as f:
        yaml.safe_dump(metadata, f, sort_keys=False, default_flow_style=False)

    return metadata


def now_iso() -> str:
    """Current UTC time as a second-precision ISO string."""
    return _now_iso()


# ---------------------------------------------------------------------------
# Version-fetcher helpers. Each returns (version, version_method).
# All swallow errors and return ("unknown", "unavailable") on failure.
# ---------------------------------------------------------------------------

def version_from_http_last_modified(url: str, *, timeout: float = 10.0) -> tuple[str, str]:
    """HTTP HEAD `url` and parse the `Last-Modified` header into YYYY-MM-DD."""
    try:
        import requests
        r = requests.head(url, allow_redirects=True, timeout=timeout)
        r.raise_for_status()
        lm = r.headers.get("Last-Modified")
        if not lm:
            return "unknown", "unavailable"
        return email.utils.parsedate_to_datetime(lm).date().isoformat(), "http_last_modified"
    except Exception:
        return "unknown", "unavailable"


def version_from_url_path(url: str, regex: str) -> tuple[str, str]:
    """Extract a version substring from the URL itself with a regex.

    The first capture group of `regex` is returned. Useful for sources like
    `bgee_v15_0`, `BIOGRID-4.4.226`, `protein.links.detailed.v12.0`, etc.
    """
    m = re.search(regex, url)
    if not m:
        return "unknown", "unavailable"
    try:
        return m.group(1), "url_path"
    except IndexError:
        return m.group(0), "url_path"


def version_from_github_release(repo: str, *, timeout: float = 10.0) -> tuple[str, str]:
    """Latest tag on a GitHub repo (e.g. "monarch-initiative/phenopacket-store")."""
    try:
        import requests
        r = requests.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()["tag_name"], "github_release_api"
    except Exception:
        return "unknown", "unavailable"


def version_from_github_branch(repo: str, *, branch: str = "main", timeout: float = 10.0) -> tuple[str, str]:
    """Latest commit SHA on a branch — for sources tracked from a moving branch HEAD."""
    try:
        import requests
        r = requests.get(
            f"https://api.github.com/repos/{repo}/branches/{branch}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        r.raise_for_status()
        sha = r.json()["commit"]["sha"][:8]
        return sha, "github_branch_head"
    except Exception:
        return "unknown", "unavailable"


def version_from_file_header(
    local_path: Path,
    *,
    pattern: str,
    comment_prefix: str = "!",
) -> tuple[str, str]:
    """Read a version line from a downloaded file's commented header.

    `pattern` is a regex with one capture group. Default `comment_prefix` is `!`,
    matching GAF and similar formats. Pass `comment_prefix='#'` for SSSOM/TSV.
    """
    try:
        regex = re.compile(pattern)
        path = Path(local_path)
        opener = open
        if path.suffix == ".gz":
            import gzip
            opener = gzip.open
        with opener(path, "rt") as f:
            for line in f:
                if not line.startswith(comment_prefix):
                    break
                m = regex.search(line)
                if m:
                    try:
                        return m.group(1).strip(), "file_header"
                    except IndexError:
                        return m.group(0).strip(), "file_header"
        return "unknown", "unavailable"
    except Exception:
        return "unknown", "unavailable"


def urls_from_download_yaml(
    path: Path,
    *,
    contains: Iterable[str] | None = None,
) -> list[str]:
    """Read URLs from an ingest's kghub-downloader config.

    Handles both supported top-level shapes — a bare list of `{url, ...}` mappings
    and a `{downloads: [...]}` wrapper.

    If `contains` is provided, only return URLs whose string representation
    contains *any* of the given substrings — useful when one download.yaml
    serves multiple logical sources.
    """
    raw = yaml.safe_load(Path(path).read_text())
    if isinstance(raw, dict) and "downloads" in raw:
        entries = raw["downloads"]
    else:
        entries = raw
    urls = [e["url"] for e in (entries or []) if isinstance(e, dict) and "url" in e]
    if contains:
        urls = [u for u in urls if any(s in u for s in contains)]
    return urls
