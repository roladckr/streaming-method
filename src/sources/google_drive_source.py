from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

import google.auth
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from src.sources.base import FileSource, ResolvedFiles, SourceQuery

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Bounded per-chunk download size. MediaIoBaseDownload writes each
# chunk straight into a real file handle on disk, so memory use never
# scales with total file size - this only bounds a single chunk.
DOWNLOAD_CHUNK_SIZE = 10 * 1024 * 1024  # 10 MiB

# Drive API page size for files().list() - the API caps this at 1000
# regardless of what's requested.
DRIVE_LIST_PAGE_SIZE = 1000


class GoogleDriveConfigError(RuntimeError):
    """
    Raised when Google Drive mode is selected but the SourceQuery is
    missing the configuration needed to find files (no file IDs and
    no search query), or a Drive search matched nothing.
    """


class GoogleDriveSource(FileSource):
    """
    Resolves files by downloading them from Google Drive to disk-
    backed temporary storage.

    Generic: contains no knowledge of any specific processor (C2,
    VCC Step 08, Air, RepLink, Call Center, Fallback, ...). Callers
    supply either explicit Drive file IDs or a Drive search query via
    SourceQuery.

    Authentication uses Application Default Credentials
    (google.auth.default()), so Cloud Run can use its attached
    service account without a credentials JSON file baked into the
    image or committed to the repo. No credentials are hardcoded here.

    `drive_service` and `downloader` are injectable so this class can
    be unit tested with fakes/mocks, without needing real Google
    credentials or network access.
    """

    def __init__(
        self,
        drive_service=None,
        downloader: (
            Callable[[object, str, Path], None] | None
        ) = None,
    ):
        self._drive_service = drive_service
        self._download = downloader or self._chunked_download

    def _get_service(self):
        if self._drive_service is None:
            credentials, _ = google.auth.default(
                scopes=DRIVE_SCOPES
            )
            self._drive_service = build(
                "drive",
                "v3",
                credentials=credentials,
                cache_discovery=False,
            )
        return self._drive_service

    def _resolve_file_entries(
        self, query: SourceQuery
    ) -> list[tuple[str, str]]:
        """Returns [(file_id, filename), ...] sorted by filename for
        deterministic ordering, matching LocalFileSource."""

        service = self._get_service()

        if query.drive_file_ids:
            entries = []
            for file_id in query.drive_file_ids:
                metadata = (
                    service.files()
                    .get(fileId=file_id, fields="id, name")
                    .execute()
                )
                entries.append(
                    (metadata["id"], metadata["name"])
                )
            return sorted(entries, key=lambda entry: entry[1])

        if query.drive_query:
            files: list[dict] = []
            page_token = None

            while True:
                request_kwargs = {
                    "q": query.drive_query,
                    "fields": "nextPageToken, files(id, name)",
                    "pageSize": DRIVE_LIST_PAGE_SIZE,
                }

                if page_token:
                    request_kwargs["pageToken"] = page_token

                response = (
                    service.files()
                    .list(**request_kwargs)
                    .execute()
                )

                files.extend(response.get("files", []))

                page_token = response.get("nextPageToken")

                if not page_token:
                    break

            if not files:
                raise GoogleDriveConfigError(
                    "Drive search matched no files for query: "
                    f"{query.drive_query!r}"
                )

            return sorted(
                ((f["id"], f["name"]) for f in files),
                key=lambda entry: entry[1],
            )

        raise GoogleDriveConfigError(
            "GoogleDriveSource requires SourceQuery.drive_file_ids "
            "or SourceQuery.drive_query - neither was provided"
        )

    def resolve(self, query: SourceQuery) -> ResolvedFiles:
        file_entries = self._resolve_file_entries(query)

        tmp_dir = tempfile.TemporaryDirectory(
            prefix="drive-source-"
        )
        tmp_root = Path(tmp_dir.name).resolve()

        try:
            service = self._get_service()
            downloaded: list[Path] = []

            for file_id, name in file_entries:
                dest = self._safe_destination(
                    tmp_root, file_id, name
                )
                self._download(service, file_id, dest)
                downloaded.append(dest)
        except Exception:
            # Don't leak the temp dir if a download fails partway.
            tmp_dir.cleanup()
            raise

        return ResolvedFiles(downloaded, cleanup=tmp_dir.cleanup)

    @staticmethod
    def _safe_destination(
        tmp_root: Path, file_id: str, name: str
    ) -> Path:
        """
        Build a unique, traversal-safe local destination for one
        downloaded file.

        Two different Drive file IDs can share the same filename, and
        Drive-provided filenames are untrusted input (they can contain
        "../" or other path segments). To handle both:

        - `.name` reduces the Drive filename to a bare basename,
          discarding any directory components (so "../../x.xlsx" and
          "nested/path.xlsx" both collapse to a safe leaf name).
        - each file is written under its own `file_id` subdirectory,
          so two files that reduce to the same basename never collide
          - and the original filename is still preserved exactly for
            debugging/logging (see `_source_file` in processor
            output), instead of being mangled with a prefix.

        The resolved path is then verified to still be inside
        `tmp_root` before any write happens, and this raises instead
        of silently writing outside the temp directory if it isn't.
        """

        safe_name = Path(name).name.strip()

        if not safe_name or safe_name in (".", ".."):
            raise ValueError(
                f"Refusing to download Drive file {file_id!r}: "
                f"unsafe or empty filename {name!r}"
            )

        dest = (tmp_root / file_id / safe_name).resolve()

        if dest != tmp_root and tmp_root not in dest.parents:
            raise ValueError(
                f"Refusing to download Drive file {file_id!r}: "
                f"resolved path {dest} escapes the temp directory "
                f"{tmp_root}"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)

        return dest

    @staticmethod
    def _chunked_download(
        service, file_id: str, dest: Path
    ) -> None:
        """
        Default download implementation: streams the file from Drive
        in bounded chunks directly into a file on disk. Never holds
        the full file in memory (no BytesIO) - this is what lets the
        large-file streaming engine work against the result exactly
        as it would against a local file.
        """

        request = service.files().get_media(fileId=file_id)

        with open(dest, "wb") as handle:
            downloader = MediaIoBaseDownload(
                handle,
                request,
                chunksize=DOWNLOAD_CHUNK_SIZE,
            )

            done = False
            while not done:
                _, done = downloader.next_chunk()
