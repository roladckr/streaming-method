from pathlib import Path
from typing import Iterable

from src.streaming.xlsx_stream import (
    stream_xlsx_rows,
)


# ---------------------------------------------------------------------------
# C2 uses these four Excel columns from B2S Car Billing.
#
# A  = Item
# D  = VAR
# G  = Order Status
# AR = ^Est Total Price
#
# The streaming engine does NOT know what these mean.
# This mapping belongs to the C2 processor.
# ---------------------------------------------------------------------------

C2_COLUMNS = {
    "Item": "A",
    "VAR": "D",
    "Order Status": "G",
    "Est Total Price": "AR",
}


def normalize_key(value) -> str:
    """
    Normalize lookup IDs so values coming from Excel,
    JSON, or n8n compare consistently.

    Examples:
        6408542     -> "6408542"
        "6408542"   -> "6408542"
        "6408542.0" -> "6408542"
    """

    if value is None:
        return ""

    value = str(value).strip()

    if value.endswith(".0"):
        value = value[:-2]

    return value


def process_single_file(
    file_path: str | Path,
    lookup_keys: set[str],
) -> dict[str, dict]:
    """
    Search one B2S XLSX file using the shared
    XLSX streaming engine.

    Only the columns required by C2 are requested.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"B2S file not found: {path}"
        )

    print(
        f"[C2] Searching {path.name} "
        f"for {len(lookup_keys)} key(s)...",
        flush=True,
    )

    found: dict[str, dict] = {}

    for streamed_row in stream_xlsx_rows(
        path,
        requested_columns=C2_COLUMNS.values(),
    ):
        item_key = normalize_key(
            streamed_row.values.get(
                C2_COLUMNS["Item"]
            )
        )

        if not item_key:
            continue

        if item_key not in lookup_keys:
            continue

        if item_key in found:
            print(
            f"[C2] Duplicate Item ignored: {item_key} "
            f"in {path.name} "
            f"row {streamed_row.row_number}",
            flush=True,
            )
            continue
    
        found[item_key] = {
            "Item": item_key,

            "VAR": streamed_row.values.get(
                C2_COLUMNS["VAR"]
            ),

            "Order Status": streamed_row.values.get(
                C2_COLUMNS["Order Status"]
            ),

            "Est Total Price": streamed_row.values.get(
                C2_COLUMNS["Est Total Price"]
            ),

            "_source_file": path.name,
            "_source_row": streamed_row.row_number,
        }
        # Stop reading this file if every remaining
        # lookup key has already been found.
        if len(found) == len(lookup_keys):
            break

    return found


def process_car_rental_c2(
    file_paths: Iterable[str | Path],
    lookup_keys: Iterable[str],
):
    """
    Search multiple B2S Car Billing files.

    Files are processed sequentially.

    Once a lookup key is found, it is removed from
    the remaining set so later files do not need
    to search for it again.
    """

    normalized_keys = {
        normalize_key(key)
        for key in lookup_keys
        if normalize_key(key)
    }

    if not normalized_keys:
        raise ValueError(
            "lookup_keys cannot be empty"
        )

    remaining_keys = set(normalized_keys)

    matches: dict[str, dict] = {}

    files_scanned: list[str] = []

    for raw_path in file_paths:
        if not remaining_keys:
            break

        path = Path(raw_path)

        files_scanned.append(
            path.name
        )

        file_matches = process_single_file(
            file_path=path,
            lookup_keys=remaining_keys,
        )

        for key, result in file_matches.items():
            matches[key] = result

            remaining_keys.discard(
                key
            )

    # Preserve the requested keys in the response
    # instead of returning arbitrary set ordering.
    ordered_matches = []

    for key in normalized_keys:
        if key in matches:
            ordered_matches.append(
                matches[key]
            )

    return {
        "matches": ordered_matches,

        "not_found": sorted(
            remaining_keys
        ),

        "requested_count": len(
            normalized_keys
        ),

        "matched_count": len(
            matches
        ),

        "files_scanned": files_scanned,
    }