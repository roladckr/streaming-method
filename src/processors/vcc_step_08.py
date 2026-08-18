import os
from pathlib import Path
from typing import Iterable

from src.sources.base import SourceQuery
from src.streaming.xlsx_stream import (
    read_header_row,
    stream_xlsx_rows,
)


# ---------------------------------------------------------------------------
# VCC Step 08 reads these Apple DAC reference columns. Unlike C2's B2S
# files, exact column letters have NOT been verified - only the header
# names, from the current n8n workflow's Google Sheet query. Column
# letters are resolved per-file from the header row (see
# resolve_dac_columns) instead of being assumed.
#
# The streaming engine does NOT know what any of this means.
# This mapping belongs to the VCC Step 08 processor.
# ---------------------------------------------------------------------------

DAC_COLUMN_NAMES = (
    "Order ID",
    "Line Num",
    "Retail Price",
    "bridge2Unit Base Price",
    "Gateway ID",
)


# Unlike CAR_RENTAL_C2's B2S_LOCAL_PATTERN, no production DAC filename
# or Drive file ID is known/verified yet, so there is no hardcoded
# default here - both must be supplied via environment configuration.
# Local mode with no pattern configured fails clearly via
# LocalFileSource's existing "requires SourceQuery.pattern" check.
VCC_STEP_08_LOCAL_PATTERN_ENV = "VCC_STEP_08_LOCAL_PATTERN"
VCC_STEP_08_DRIVE_FILE_IDS_ENV = "VCC_STEP_08_DRIVE_FILE_IDS"


def vcc_step_08_source_query() -> SourceQuery:
    """
    Builds the SourceQuery describing where the Apple DAC reference
    file(s) come from. Read at call time (not import time) so the
    active FILE_SOURCE/env configuration is always honored.
    """

    local_pattern = os.environ.get(
        VCC_STEP_08_LOCAL_PATTERN_ENV, ""
    )

    raw_ids = os.environ.get(
        VCC_STEP_08_DRIVE_FILE_IDS_ENV, ""
    )

    drive_file_ids = tuple(
        file_id.strip()
        for file_id in raw_ids.split(",")
        if file_id.strip()
    )

    return SourceQuery(
        pattern=local_pattern,
        drive_file_ids=drive_file_ids,
    )


def resolve_dac_columns(
    file_path: str | Path,
) -> dict[str, str]:
    """
    Resolve DAC column letters by header name for one file.

    Column positions are not assumed - discovered from the header
    row via the shared streaming engine's generic header reader, so
    this keeps working even if the real DAC file's layout doesn't
    match B2S-style fixed positions.
    """

    headers = read_header_row(file_path)

    missing = [
        name
        for name in DAC_COLUMN_NAMES
        if name not in headers
    ]

    if missing:
        raise ValueError(
            f"{Path(file_path).name} is missing expected DAC "
            f"header(s): {', '.join(missing)}"
        )

    return {
        name: headers[name] for name in DAC_COLUMN_NAMES
    }


def normalize_order_id(value) -> str:
    """
    Normalize Order IDs so values coming from Excel (which may be
    numeric) compare consistently with string JSON lookup keys from
    n8n.

    Examples:
        1301771237     -> "1301771237"
        "1301771237"   -> "1301771237"
        "1301771237.0" -> "1301771237"
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
) -> tuple[list[dict], set[str]]:
    """
    Search one DAC XLSX file using the shared XLSX streaming engine.

    Returns EVERY row whose Order ID is in `lookup_keys` - not
    deduplicated. One Order ID may have multiple Line Num rows, and
    all of them are returned (unlike C2's first-occurrence-wins
    behavior, which does not apply here).
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"DAC file not found: {path}"
        )

    columns = resolve_dac_columns(path)

    print(
        f"[VCC_STEP_08] Searching {path.name} "
        f"for {len(lookup_keys)} Order ID(s)...",
        flush=True,
    )

    matched_rows: list[dict] = []
    found_order_ids: set[str] = set()

    for streamed_row in stream_xlsx_rows(
        path,
        requested_columns=columns.values(),
    ):
        order_id = normalize_order_id(
            streamed_row.values.get(
                columns["Order ID"]
            )
        )

        if not order_id:
            continue

        if order_id not in lookup_keys:
            continue

        found_order_ids.add(order_id)

        matched_rows.append({
            "Order ID": order_id,

            "Line Num": streamed_row.values.get(
                columns["Line Num"]
            ),

            "Retail Price": streamed_row.values.get(
                columns["Retail Price"]
            ),

            "bridge2Unit Base Price": streamed_row.values.get(
                columns["bridge2Unit Base Price"]
            ),

            # Not currently used as the join key downstream, but
            # returned so n8n Step 8b can switch to it without
            # changing this parser - see process_vcc_step_08 below.
            "Gateway ID": streamed_row.values.get(
                columns["Gateway ID"]
            ),

            "_source_file": path.name,
            "_source_row": streamed_row.row_number,
        })

        # Deliberately no early exit here (unlike C2's "stop once
        # every key is found"): the same Order ID can have further
        # Line Num rows later in this file, so scanning must continue
        # even after a match.

    return matched_rows, found_order_ids


def process_vcc_step_08(
    file_paths: Iterable[str | Path],
    lookup_keys: Iterable[str],
):
    """
    Search one or more Apple DAC reference files for every row
    matching any of the requested Order IDs.

    Each source file is scanned exactly once, in full - the full
    requested Order ID set is searched against every file (not
    shrunk as matches are found), because a matching Order ID can
    have additional Line Num rows later in the same file or in a
    file scanned later.

    Join key note:
    The current n8n Step 8b joins on Order ID + Line Num, but that
    key is UNVERIFIED with the business - Gateway ID may turn out to
    be the correct key instead. This is not a confirmed business
    rule. Gateway ID is returned on every row specifically so
    downstream logic can switch keys later without any change to
    this parser.
    """

    normalized_keys = {
        normalize_order_id(key)
        for key in lookup_keys
        if normalize_order_id(key)
    }

    if not normalized_keys:
        raise ValueError(
            "lookup_keys cannot be empty"
        )

    all_matches: list[dict] = []
    found_order_ids: set[str] = set()
    files_scanned: list[str] = []

    for raw_path in file_paths:
        path = Path(raw_path)

        files_scanned.append(path.name)

        file_matches, found_in_file = process_single_file(
            file_path=path,
            lookup_keys=normalized_keys,
        )

        all_matches.extend(file_matches)
        found_order_ids |= found_in_file

    not_found = sorted(
        normalized_keys - found_order_ids
    )

    return {
        "matches": all_matches,

        "not_found": not_found,

        "requested_count": len(
            normalized_keys
        ),

        "matched_order_count": len(
            found_order_ids
        ),

        "matched_row_count": len(
            all_matches
        ),

        "files_scanned": files_scanned,
    }
