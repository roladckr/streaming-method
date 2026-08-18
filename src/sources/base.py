from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence


@dataclass(frozen=True)
class SourceQuery:
    """
    Describes where a processor's source files come from, in a way
    that's meaningful to any FileSource implementation.

    A processor builds exactly one SourceQuery; each FileSource reads
    only the fields it understands:

    - `pattern`: a glob pattern, used by LocalFileSource.
    - `drive_file_ids`: explicit Google Drive file IDs, used by
      GoogleDriveSource.
    - `drive_query`: a Google Drive search query, used by
      GoogleDriveSource as an alternative to explicit file IDs.

    This is what lets a processor stay agnostic about whether it's
    reading from local disk or Google Drive - it just builds one
    query and hands it to whatever source is configured.
    """

    pattern: str = ""
    drive_file_ids: tuple[str, ...] = field(default_factory=tuple)
    drive_query: str | None = None


class ResolvedFiles(AbstractContextManager):
    """
    Context manager wrapping the local filesystem Paths a FileSource
    resolved for a processor to read.

    For sources backed by files that already live on local disk
    (LocalFileSource), exiting the context does nothing. For sources
    that download files to temporary storage (GoogleDriveSource),
    exiting the context removes those temporary files - callers don't
    need to know which case they're in.

        with source.resolve(query) as paths:
            ...  # paths are guaranteed valid for the block
        # any temporary downloads are now cleaned up
    """

    def __init__(
        self,
        paths: Sequence[Path],
        cleanup: Callable[[], None] | None = None,
    ):
        self.paths: list[Path] = list(paths)
        self._cleanup = cleanup

    def __enter__(self) -> list[Path]:
        return self.paths

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._cleanup is not None:
            self._cleanup()
        return False


class NoSourceFilesResolvedError(RuntimeError):
    """
    Raised when a FileSource resolved zero files for an operation
    that requires at least one source file to search.

    This is deliberately distinct from a processor returning zero
    matches: matched_count == 0 is a legitimate result when source
    files exist but none contain the requested lookup keys. Zero
    *resolved files* means there was nothing to search in the first
    place (e.g. a local glob pattern matched nothing, or a caller
    ignored an empty result rather than treating it as an error), and
    accounting workflows must never see that look like a normal
    zero-match response.
    """


class FileSource(ABC):
    """
    Resolves a SourceQuery to local filesystem Path objects the
    existing streaming engine (src/streaming/xlsx_stream.py) can read.

    Implementations must not contain knowledge of any specific
    processor (e.g. CAR_RENTAL_C2) - that knowledge belongs to the
    processor module, which builds the SourceQuery.
    """

    @abstractmethod
    def resolve(self, query: SourceQuery) -> ResolvedFiles:
        """Resolve `query` to zero or more local files. Must be used
        as a context manager - see ResolvedFiles."""
        raise NotImplementedError
