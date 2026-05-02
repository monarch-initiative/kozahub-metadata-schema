try:
    from kozahub_metadata_schema._version import __version__, __version_tuple__
except ImportError:  # pragma: no cover
    __version__ = "0.0.0"
    __version_tuple__ = (0, 0, 0)

from kozahub_metadata_schema.writer import (  # noqa: E402,F401
    now_iso,
    urls_from_download_yaml,
    version_from_file_header,
    version_from_github_branch,
    version_from_github_release,
    version_from_http_last_modified,
    version_from_url_path,
    write_metadata,
)
