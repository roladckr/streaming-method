import os
from pathlib import Path

from flask import Flask, jsonify, request

from src.processors.car_rental_c2 import (
    c2_source_query,
    process_car_rental_c2,
)
from src.processors.vcc_step_08 import (
    process_vcc_step_08,
    vcc_step_08_source_query,
)
from src.sources.base import NoSourceFilesResolvedError
from src.sources.google_drive_source import (
    GoogleDriveConfigError,
    GoogleDriveSource,
)
from src.sources.local_source import LocalFileSource


app = Flask(__name__)


BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DATA_DIR = BASE_DIR / "data"

FILE_SOURCE_ENV = "FILE_SOURCE"
DEFAULT_FILE_SOURCE = "local"


# Dispatch table: operation name -> (source query builder, processor).
# This is pure configuration - app.py stays a thin dispatcher and
# never contains column mappings, business rules, or math for any
# operation. Adding an operation means adding one entry here.
OPERATIONS = {
    "CAR_RENTAL_C2": (c2_source_query, process_car_rental_c2),
    "VCC_STEP_08": (
        vcc_step_08_source_query,
        process_vcc_step_08,
    ),
}

SUPPORTED_OPERATIONS = set(OPERATIONS)


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
        build_query, run_processor = OPERATIONS[operation]

        source = _get_file_source()

        with source.resolve(build_query()) as file_paths:
            if not file_paths:
                # Zero resolved source files is not the same thing as
                # zero matches: a processor returning matched_count=0
                # must mean "files were searched, key wasn't in them",
                # never "there was nothing to search". Fail explicitly
                # instead of letting this look like a normal
                # zero-match response.
                raise NoSourceFilesResolvedError(
                    f"No source files resolved for operation "
                    f"{operation!r}."
                )

            result = run_processor(
                file_paths=file_paths,
                lookup_keys=lookup_keys,
            )

        return jsonify({
            "status": "success",
            "operation": operation,
            **result,
        })

    except NoSourceFilesResolvedError as error:
        return jsonify({
            "status": "error",
            "type": "NO_SOURCE_FILES",
            "message": str(error),
        }), 503

    except GoogleDriveConfigError as error:
        return jsonify({
            "status": "error",
            "type": "SOURCE_CONFIG_ERROR",
            "message": str(error),
        }), 503

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