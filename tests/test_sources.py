import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.processors.car_rental_c2 import (
    B2S_DRIVE_FILE_IDS_ENV,
    c2_source_query,
)
from src.sources.base import SourceQuery
from src.sources.google_drive_source import (
    GoogleDriveConfigError,
    GoogleDriveSource,
)
from src.sources.local_source import LocalFileSource


class LocalFileSourceTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.base_dir = Path(self._tmpdir.name)

        # Deliberately created out of order, to prove resolve() sorts.
        for name in [
            "B2S_Car_Billing_part3.xlsx",
            "B2S_Car_Billing_part1.xlsx",
            "B2S_Car_Billing_part2.xlsx",
            "unrelated_file.xlsx",
        ]:
            (self.base_dir / name).write_bytes(b"fake")

    def test_resolves_sorted_deterministic_files(self):
        source = LocalFileSource(self.base_dir)

        with source.resolve(
            SourceQuery(pattern="B2S_Car_Billing_part*.xlsx")
        ) as paths:
            names = [path.name for path in paths]

        self.assertEqual(
            names,
            [
                "B2S_Car_Billing_part1.xlsx",
                "B2S_Car_Billing_part2.xlsx",
                "B2S_Car_Billing_part3.xlsx",
            ],
        )

    def test_pattern_matching_nothing_returns_empty_list(self):
        source = LocalFileSource(self.base_dir)

        with source.resolve(
            SourceQuery(pattern="no_such_file_*.xlsx")
        ) as paths:
            self.assertEqual(paths, [])

    def test_missing_base_dir_raises_clear_error(self):
        source = LocalFileSource(self.base_dir / "does_not_exist")

        with self.assertRaises(FileNotFoundError):
            source.resolve(SourceQuery(pattern="*.xlsx"))

    def test_resolve_without_pattern_raises(self):
        source = LocalFileSource(self.base_dir)

        with self.assertRaises(ValueError):
            source.resolve(SourceQuery())


class C2SourceQueryTests(unittest.TestCase):
    def test_no_env_var_produces_empty_drive_file_ids(self):
        with patch.dict(
            os.environ, {}, clear=False
        ):
            os.environ.pop(B2S_DRIVE_FILE_IDS_ENV, None)
            query = c2_source_query()

        self.assertEqual(query.drive_file_ids, ())
        self.assertEqual(
            query.pattern, "B2S_Car_Billing_part*.xlsx"
        )

    def test_env_var_parses_comma_separated_ids(self):
        with patch.dict(
            os.environ,
            {B2S_DRIVE_FILE_IDS_ENV: " id-1 ,id-2,,id-3 "},
        ):
            query = c2_source_query()

        self.assertEqual(
            query.drive_file_ids, ("id-1", "id-2", "id-3")
        )


class FakeExecutable:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeFilesResource:
    """
    Models Google Drive's files().list() pagination contract:
    pageSize, pageToken, and nextPageToken.

    `list_pages` maps a query string to a list of pages, where each
    page is the list of file dicts for that page. `list()` returns
    one page per call, threading pageToken as an opaque page index
    and only ever setting nextPageToken when a further page exists -
    matching how the real Drive API behaves (no nextPageToken on the
    last page).
    """

    def __init__(self, metadata_by_id=None, list_pages=None):
        self._metadata_by_id = metadata_by_id or {}
        self._list_pages = list_pages or {}

    def get(self, fileId, fields=None):
        return FakeExecutable(self._metadata_by_id[fileId])

    def list(self, q=None, fields=None, pageSize=None, pageToken=None):
        pages = self._list_pages.get(q, [])

        page_index = int(pageToken) if pageToken else 0

        if page_index >= len(pages):
            return FakeExecutable({"files": []})

        result = {"files": pages[page_index]}

        if page_index + 1 < len(pages):
            result["nextPageToken"] = str(page_index + 1)

        return FakeExecutable(result)

    def get_media(self, fileId):
        return ("media-request", fileId)


class FakeDriveService:
    def __init__(self, metadata_by_id=None, list_pages=None):
        self._files = FakeFilesResource(
            metadata_by_id, list_pages
        )

    def files(self):
        return self._files


def fake_downloader_writing(content_by_id):
    def _download(service, file_id, dest: Path) -> None:
        dest.write_bytes(content_by_id[file_id])

    return _download


class GoogleDriveSourceConfigValidationTests(unittest.TestCase):
    def test_missing_file_ids_and_query_raises_config_error(self):
        source = GoogleDriveSource(
            drive_service=FakeDriveService()
        )

        with self.assertRaises(GoogleDriveConfigError):
            source.resolve(SourceQuery())

    def test_search_query_matching_nothing_raises_config_error(self):
        source = GoogleDriveSource(
            drive_service=FakeDriveService(
                list_pages={"name contains 'X'": [[]]}
            )
        )

        with self.assertRaises(GoogleDriveConfigError):
            source.resolve(
                SourceQuery(drive_query="name contains 'X'")
            )


class GoogleDriveSourceMockedTests(unittest.TestCase):
    def test_resolves_by_explicit_file_ids_sorted_by_name(self):
        service = FakeDriveService(
            metadata_by_id={
                "id-b": {"id": "id-b", "name": "b.xlsx"},
                "id-a": {"id": "id-a", "name": "a.xlsx"},
            }
        )

        downloader = fake_downloader_writing(
            {"id-a": b"AAAA", "id-b": b"BBBB"}
        )

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_file_ids=("id-b", "id-a"))
        ) as paths:
            names = [path.name for path in paths]
            contents = [path.read_bytes() for path in paths]

        self.assertEqual(names, ["a.xlsx", "b.xlsx"])
        self.assertEqual(contents, [b"AAAA", b"BBBB"])

    def test_resolves_by_search_query(self):
        service = FakeDriveService(
            list_pages={
                "name contains 'B2S'": [
                    [
                        {"id": "id-1", "name": "one.xlsx"},
                        {"id": "id-2", "name": "two.xlsx"},
                    ]
                ]
            }
        )

        downloader = fake_downloader_writing(
            {"id-1": b"one", "id-2": b"two"}
        )

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_query="name contains 'B2S'")
        ) as paths:
            names = sorted(path.name for path in paths)

        self.assertEqual(names, ["one.xlsx", "two.xlsx"])

    def test_search_query_results_are_returned_in_deterministic_order(
        self,
    ):
        # Deliberately NOT sorted by the test before asserting - this
        # proves the implementation itself produces deterministic
        # sorted-by-name output, rather than the test masking
        # out-of-order results by sorting them itself.
        service = FakeDriveService(
            list_pages={
                "name contains 'B2S'": [
                    [
                        {"id": "id-2", "name": "zeta.xlsx"},
                        {"id": "id-3", "name": "alpha.xlsx"},
                        {"id": "id-1", "name": "mid.xlsx"},
                    ]
                ]
            }
        )

        downloader = fake_downloader_writing(
            {"id-1": b"m", "id-2": b"z", "id-3": b"a"}
        )

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_query="name contains 'B2S'")
        ) as paths:
            names = [path.name for path in paths]

        self.assertEqual(
            names, ["alpha.xlsx", "mid.xlsx", "zeta.xlsx"]
        )

    def test_search_query_paginates_and_accumulates_all_pages(self):
        service = FakeDriveService(
            list_pages={
                "name contains 'B2S'": [
                    [{"id": "id-3", "name": "c.xlsx"}],
                    [{"id": "id-1", "name": "a.xlsx"}],
                    [{"id": "id-2", "name": "b.xlsx"}],
                ]
            }
        )

        downloader = fake_downloader_writing(
            {"id-1": b"A", "id-2": b"B", "id-3": b"C"}
        )

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_query="name contains 'B2S'")
        ) as paths:
            names = [path.name for path in paths]

        # All three pages accumulated, no duplicates, and sorted
        # deterministically by the implementation - not by page order.
        self.assertEqual(names, ["a.xlsx", "b.xlsx", "c.xlsx"])

    def test_downloaded_files_are_cleaned_up_after_context_exit(
        self,
    ):
        service = FakeDriveService(
            metadata_by_id={
                "id-a": {"id": "id-a", "name": "a.xlsx"},
            }
        )

        downloader = fake_downloader_writing({"id-a": b"AAAA"})

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_file_ids=("id-a",))
        ) as paths:
            captured_paths = list(paths)
            for path in captured_paths:
                self.assertTrue(path.exists())

        for path in captured_paths:
            self.assertFalse(path.exists())
        self.assertFalse(captured_paths[0].parent.exists())

    def test_duplicate_drive_filenames_stay_distinct(self):
        # Two different Drive file IDs sharing the same filename -
        # Drive allows this. Neither downloaded file may overwrite
        # the other, and both must retain their own distinct content.
        service = FakeDriveService(
            metadata_by_id={
                "id-1": {"id": "id-1", "name": "report.xlsx"},
                "id-2": {"id": "id-2", "name": "report.xlsx"},
            }
        )

        downloader = fake_downloader_writing(
            {"id-1": b"CONTENT-ONE", "id-2": b"CONTENT-TWO"}
        )

        source = GoogleDriveSource(
            drive_service=service, downloader=downloader
        )

        with source.resolve(
            SourceQuery(drive_file_ids=("id-1", "id-2"))
        ) as paths:
            self.assertEqual(len(paths), 2)

            # Distinct local paths, even though both share a
            # basename.
            self.assertEqual(
                len({str(path) for path in paths}), 2
            )

            contents = {
                path: path.read_bytes() for path in paths
            }

        distinct_contents = set(contents.values())
        self.assertEqual(
            distinct_contents, {b"CONTENT-ONE", b"CONTENT-TWO"}
        )

    def test_path_traversal_filenames_are_sanitized(self):
        dangerous_names = {
            "id-1": "../x.xlsx",
            "id-2": "../../x.xlsx",
            "id-3": "nested/path.xlsx",
        }

        metadata_by_id = {
            file_id: {"id": file_id, "name": name}
            for file_id, name in dangerous_names.items()
        }

        downloader = fake_downloader_writing(
            {file_id: b"DATA" for file_id in dangerous_names}
        )

        source = GoogleDriveSource(
            drive_service=FakeDriveService(
                metadata_by_id=metadata_by_id
            ),
            downloader=downloader,
        )

        with source.resolve(
            SourceQuery(
                drive_file_ids=tuple(dangerous_names)
            )
        ) as paths:
            self.assertEqual(len(paths), 3)

            # tmp_root is two levels above each file (tmp_root /
            # file_id / basename).
            tmp_root = paths[0].parent.parent.resolve()

            for path in paths:
                resolved = path.resolve()

                self.assertTrue(
                    resolved == tmp_root
                    or tmp_root in resolved.parents,
                    f"{resolved} escaped temp directory {tmp_root}",
                )
                self.assertNotIn("..", resolved.parts)
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(), b"DATA")

    def test_temp_dir_not_leaked_when_download_fails(self):
        service = FakeDriveService(
            metadata_by_id={
                "id-a": {"id": "id-a", "name": "a.xlsx"},
            }
        )

        captured_dirs = []

        def failing_downloader(service, file_id, dest: Path):
            captured_dirs.append(dest.parent)
            raise RuntimeError("simulated download failure")

        source = GoogleDriveSource(
            drive_service=service, downloader=failing_downloader
        )

        with self.assertRaises(RuntimeError):
            source.resolve(
                SourceQuery(drive_file_ids=("id-a",))
            )

        self.assertTrue(captured_dirs)
        self.assertFalse(captured_dirs[0].exists())


class FileSourceEnvSelectionTests(unittest.TestCase):
    def test_default_and_explicit_local_mode(self):
        from src.app import _get_file_source

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FILE_SOURCE", None)
            source = _get_file_source()
        self.assertEqual(
            type(source).__name__, "LocalFileSource"
        )

        with patch.dict(os.environ, {"FILE_SOURCE": "local"}):
            source = _get_file_source()
        self.assertEqual(
            type(source).__name__, "LocalFileSource"
        )

    def test_google_drive_mode(self):
        from src.app import _get_file_source

        with patch.dict(
            os.environ, {"FILE_SOURCE": "google_drive"}
        ):
            source = _get_file_source()
        self.assertEqual(
            type(source).__name__, "GoogleDriveSource"
        )

    def test_invalid_mode_raises_clearly(self):
        from src.app import _get_file_source

        with patch.dict(
            os.environ, {"FILE_SOURCE": "dropbox"}
        ):
            with self.assertRaises(ValueError):
                _get_file_source()


if __name__ == "__main__":
    unittest.main()
