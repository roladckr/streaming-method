from __future__ import annotations

from pathlib import Path

from src.sources.base import FileSource, ResolvedFiles, SourceQuery


class LocalFileSource(FileSource):
    """
    Resolves files from a configurable local filesystem directory
    using glob pattern matching. Used for local development, and as
    the FILE_SOURCE=local production fallback.
    """

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)

    def resolve(self, query: SourceQuery) -> ResolvedFiles:
        if not query.pattern:
            raise ValueError(
                "LocalFileSource requires SourceQuery.pattern"
            )

        if not self.base_dir.exists():
            raise FileNotFoundError(
                f"Local source directory not found: {self.base_dir}"
            )

        # Sorted for deterministic, repeatable file ordering (e.g.
        # part1 before part2), matching prior hardcoded behavior.
        # A pattern that simply matches nothing is not an error here -
        # that's left to the processor/caller to decide how to react.
        paths = sorted(self.base_dir.glob(query.pattern))

        return ResolvedFiles(paths)
