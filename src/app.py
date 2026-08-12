import os
from pathlib import Path

from flask import Flask, jsonify, request

from src.processors.car_rental_c2 import (
    c2_source_query,
    process_car_rental_c2,
)
from src.sources.google_drive_source import GoogleDriveSource
from src.sources.local_source import LocalFileSource


app = Flask(__name__)


BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DATA_DIR = BASE_DIR / "data"

FILE_SOURCE_ENV = "FILE_SOURCE"
DEFAULT_FILE_SOURCE = "local"


SUPPORTED_OPERATIONS = {
    "CAR_RENTAL_C2",
}


def _get_file_source():
    """
    Selects the configured file source provider. Generic across
    operations - contains no C2-specific (or any processor-specific)
    knowledge.

    FILE_SOURCE=local (default): read from the local data/ directory.
    FILE_SOURCE=google_drive: download from Google Drive using
    Application Default Credentials.
    """

    mode = (
        os.environ.get(FILE_SOURCE_ENV, DEFAULT_FILE_SOURCE)
        .strip()
        .lower()
    )

    if mode == "local":
        return LocalFileSource(LOCAL_DATA_DIR)

    if mode == "google_drive":
        return GoogleDriveSource()

    raise ValueError(
        f"Unsupported {FILE_SOURCE_ENV}: {mode!r} "
        "(expected 'local' or 'google_drive')"
    )


@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "pcard-file-service",
        "supported_operations": sorted(
            SUPPORTED_OPERATIONS
        ),
    })


@app.post("/process")
def process():
    payload = request.get_json(
        silent=True
    )

    if not payload:
        return jsonify({
            "status": "error",
            "message": "JSON body is required",
        }), 400

    operation = payload.get("operation")

    if not operation:
        return jsonify({
            "status": "error",
            "message": "operation is required",
        }), 400

    if operation not in SUPPORTED_OPERATIONS:
        return jsonify({
            "status": "error",
            "message": (
                f"Unsupported operation: "
                f"{operation}"
            ),
            "supported_operations": sorted(
                SUPPORTED_OPERATIONS
            ),
        }), 400

    lookup_keys = payload.get(
        "lookup_keys",
        []
    )

    if not isinstance(lookup_keys, list):
        return jsonify({
            "status": "error",
            "message": (
                "lookup_keys must be an array"
            ),
        }), 400

    if not lookup_keys:
        return jsonify({
            "status": "error",
            "message": (
                "lookup_keys cannot be empty"
            ),
        }), 400

    try:
        if operation == "CAR_RENTAL_C2":
            source = _get_file_source()

            with source.resolve(
                c2_source_query()
            ) as file_paths:
                result = process_car_rental_c2(
                    file_paths=file_paths,
                    lookup_keys=lookup_keys,
                )

            return jsonify({
                "status": "success",
                "operation": operation,
                **result,
            })

        return jsonify({
            "status": "error",
            "message": (
                "Operation handler not implemented"
            ),
        }), 501

    except FileNotFoundError as error:
        return jsonify({
            "status": "error",
            "type": "FILE_NOT_FOUND",
            "message": str(error),
        }), 500

    except ValueError as error:
        return jsonify({
            "status": "error",
            "type": "INVALID_DATA",
            "message": str(error),
        }), 400

    except Exception as error:
        app.logger.exception(
            "Unexpected processing error"
        )

        return jsonify({
            "status": "error",
            "type": "INTERNAL_ERROR",
            "message": str(error),
        }), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=8080,
        debug=False,
    )