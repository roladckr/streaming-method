import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.app import app
from src.sources.google_drive_source import GoogleDriveConfigError


class HealthEndpointTests(unittest.TestCase):
    """
    /health must work without any Drive configuration present -
    Cloud Run health/readiness checks must not depend on
    CAR_RENTAL_C2_DRIVE_FILE_IDS, VCC_STEP_08_DRIVE_FILE_IDS, or any
    other FILE_SOURCE-specific env var being set.
    """

    def setUp(self):
        self.client = app.test_client()

    def test_health_ok_with_no_file_source_configured(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FILE_SOURCE", None)
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")

    def test_health_ok_with_google_drive_selected_but_no_ids(self):
        with patch.dict(
            os.environ, {"FILE_SOURCE": "google_drive"}
        ):
            for key in (
                "CAR_RENTAL_C2_DRIVE_FILE_IDS",
                "VCC_STEP_08_DRIVE_FILE_IDS",
            ):
                os.environ.pop(key, None)

            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ok")


class ProcessZeroSourceFilesTests(unittest.TestCase):
    """
    Zero resolved source files must fail /process explicitly instead
    of coming back indistinguishable from a legitimate zero-match
    result (matched_count == 0).
    """

    def setUp(self):
        self.client = app.test_client()

    def test_local_source_resolving_nothing_fails_closed(self):
        with tempfile.TemporaryDirectory() as empty_dir:
            with patch.dict(
                os.environ, {"FILE_SOURCE": "local"}
            ):
                with patch(
                    "src.app.LOCAL_DATA_DIR", Path(empty_dir)
                ):
                    response = self.client.post(
                        "/process",
                        json={
                            "operation": "CAR_RENTAL_C2",
                            "lookup_keys": ["6408542"],
                        },
                    )

        body = response.get_json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["type"], "NO_SOURCE_FILES")

    def test_google_drive_config_error_returns_clear_source_error(
        self,
    ):
        with patch.dict(
            os.environ, {"FILE_SOURCE": "google_drive"}
        ):
            with patch("src.app.GoogleDriveSource") as mock_cls:
                mock_cls.return_value.resolve.side_effect = (
                    GoogleDriveConfigError(
                        "GoogleDriveSource requires "
                        "SourceQuery.drive_file_ids or "
                        "SourceQuery.drive_query - neither was "
                        "provided"
                    )
                )

                response = self.client.post(
                    "/process",
                    json={
                        "operation": "CAR_RENTAL_C2",
                        "lookup_keys": ["6408542"],
                    },
                )

        body = response.get_json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["type"], "SOURCE_CONFIG_ERROR")
        self.assertNotEqual(body["status"], "success")


if __name__ == "__main__":
    unittest.main()
