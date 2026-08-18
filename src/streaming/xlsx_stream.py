from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator
from zipfile import ZipFile
import re
import xml.etree.ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass
class StreamedRow:
    row_number: int
    values: dict[str, object]


def normalize_column_letter(value: str) -> str:
    """
    Normalize Excel column letters.

    Examples:
        a  -> A
        ar -> AR
    """

    value = str(value).strip().upper()

    if not re.fullmatch(r"[A-Z]+", value):
        raise ValueError(
            f"Invalid Excel column letter: {value}"
        )

    return value


def cell_reference_to_column(reference: str) -> str:
    """
    Convert:
        A25  -> A
        AR2  -> AR
    """

    match = re.match(
        r"([A-Z]+)",
        reference.upper(),
    )

    if not match:
        return ""

    return match.group(1)


def _iter_shared_strings(source) -> Iterator[str]:
    """
    Stream-decode xl/sharedStrings.xml one <si> entry at a time.

    Split out from load_shared_strings() so the same memory-bounded
    technique used by stream_xlsx_rows can be exercised directly by a
    regression test (see tests/test_xlsx_stream.py).

    Like sheetData's <row> children, cleared <si> elements stay
    attached as empty children of the <sst> root for the life of the
    parse unless explicitly detached - so si elements are removed
    from the root immediately after being decoded, the same way
    stream_xlsx_rows detaches processed <row> elements from
    sheetData.
    """

    si_tag = f"{{{MAIN_NS}}}si"

    context = ET.iterparse(
        source,
        events=("start", "end"),
    )

    root = None

    for event, elem in context:
        if event == "start":
            if root is None:
                root = elem
            continue

        if elem.tag != si_tag:
            continue

        text_parts = []

        for text_node in elem.iter(
            f"{{{MAIN_NS}}}t"
        ):
            if text_node.text is not None:
                text_parts.append(
                    text_node.text
                )

        yield "".join(text_parts)

        elem.clear()

        if root is not None:
            root.remove(elem)


def load_shared_strings(
    archive: ZipFile,
) -> list[str]:
    """
    Load Excel's shared string table, if present.

    XLSX files often store text cells as indexes into
    xl/sharedStrings.xml.
    """

    shared_strings_path = "xl/sharedStrings.xml"

    if shared_strings_path not in archive.namelist():
        return []

    with archive.open(shared_strings_path) as source:
        return list(_iter_shared_strings(source))


def get_first_worksheet_path(
    archive: ZipFile,
) -> str:
    """
    Resolve the first worksheet path from workbook.xml
    instead of assuming sheet1.xml.
    """

    workbook_xml = "xl/workbook.xml"
    workbook_rels = (
        "xl/_rels/workbook.xml.rels"
    )

    with archive.open(workbook_xml) as source:
        workbook_tree = ET.parse(source)

    root = workbook_tree.getroot()

    sheets = root.find(
        f"{{{MAIN_NS}}}sheets"
    )

    if sheets is None or len(sheets) == 0:
        raise ValueError(
            "Workbook contains no worksheets."
        )

    first_sheet = sheets[0]

    relationship_id = first_sheet.attrib.get(
        f"{{{REL_NS}}}id"
    )

    if not relationship_id:
        raise ValueError(
            "Could not resolve worksheet relationship."
        )

    with archive.open(workbook_rels) as source:
        relationships_tree = ET.parse(source)

    relationships_root = (
        relationships_tree.getroot()
    )

    for relationship in relationships_root:
        if (
            relationship.attrib.get("Id")
            == relationship_id
        ):
            target = relationship.attrib.get(
                "Target"
            )

            if not target:
                break

            target = target.lstrip("/")

            if target.startswith("xl/"):
                return target

            return f"xl/{target}"

    raise ValueError(
        "Could not resolve worksheet XML path."
    )


def parse_cell_value(
    cell: ET.Element,
    shared_strings: list[str],
):
    """
    Convert one XLSX XML cell to a Python value.

    Supports:
    - shared strings
    - inline strings
    - strings
    - booleans
    - numeric cells
    - cached formula values
    """

    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        inline = cell.find(
            f"{{{MAIN_NS}}}is"
        )

        if inline is None:
            return ""

        parts = []

        for text_node in inline.iter(
            f"{{{MAIN_NS}}}t"
        ):
            if text_node.text is not None:
                parts.append(
                    text_node.text
                )

        return "".join(parts)

    value_node = cell.find(
        f"{{{MAIN_NS}}}v"
    )

    if value_node is None:
        return None

    raw_value = value_node.text

    if raw_value is None:
        return None

    if cell_type == "s":
        try:
            index = int(raw_value)

            return shared_strings[index]
        except (
            ValueError,
            IndexError,
        ):
            return raw_value

    if cell_type == "str":
        return raw_value

    if cell_type == "b":
        return raw_value == "1"

    # Default XLSX cells are normally numeric.
    try:
        number = float(raw_value)

        if number.is_integer():
            return int(number)

        return number

    except ValueError:
        return raw_value


def read_header_row(
    file_path: str | Path,
    header_row_number: int = 1,
) -> dict[str, str]:
    """
    Read one header row from the first worksheet and return a mapping
    of header text -> Excel column letter.

    Generic, contains no business logic. Lets a processor that
    doesn't have a verified fixed column layout (unlike CAR_RENTAL_C2,
    which uses known column letters directly) resolve column letters
    by header name before calling stream_xlsx_rows.

    Stops as soon as the header row has been read - it does not scan
    the rest of the file.

    Example:

        headers = read_header_row(path)
        # {"Order ID": "B", "Line Num": "C", ...}

        stream_xlsx_rows(
            path,
            requested_columns=[headers["Order ID"]],
        )
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"XLSX file not found: {path}"
        )

    row_tag = f"{{{MAIN_NS}}}row"

    with ZipFile(path, "r") as archive:
        shared_strings = load_shared_strings(archive)
        worksheet_path = get_first_worksheet_path(archive)

        with archive.open(
            worksheet_path
        ) as worksheet_source:

            context = ET.iterparse(
                worksheet_source,
                events=("end",),
            )

            for event, elem in context:
                if elem.tag != row_tag:
                    continue

                row_number_raw = elem.attrib.get("r")

                try:
                    row_number = int(row_number_raw)
                except (TypeError, ValueError):
                    row_number = -1

                if row_number != header_row_number:
                    elem.clear()

                    if row_number > header_row_number:
                        break

                    continue

                headers: dict[str, str] = {}

                for cell in elem.findall(
                    f"{{{MAIN_NS}}}c"
                ):
                    reference = cell.attrib.get("r", "")
                    column = cell_reference_to_column(
                        reference
                    )
                    value = parse_cell_value(
                        cell, shared_strings
                    )

                    if value is None:
                        continue

                    header_text = str(value).strip()

                    if header_text:
                        headers[header_text] = column

                elem.clear()

                return headers

    raise ValueError(
        f"Header row {header_row_number} not found in "
        f"{path.name}"
    )


def stream_xlsx_rows(
    file_path: str | Path,
    requested_columns: Iterable[str],
) -> Iterator[StreamedRow]:
    """
    Stream selected columns from the first worksheet
    of an XLSX file.

    IMPORTANT:
    This function contains NO P-Card business logic.

    Example:

        stream_xlsx_rows(
            file_path,
            requested_columns=[
                "A",
                "D",
                "G",
                "AR",
            ],
        )

    Each yielded row looks like:

        StreamedRow(
            row_number=100,
            values={
                "A": "6408542",
                "D": "BAC",
                "G": "Ordered",
                "AR": 357.69,
            },
        )
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(
            f"XLSX file not found: {path}"
        )

    wanted_columns = {
        normalize_column_letter(column)
        for column in requested_columns
    }

    if not wanted_columns:
        raise ValueError(
            "requested_columns cannot be empty"
        )

    with ZipFile(path, "r") as archive:
        shared_strings = load_shared_strings(
            archive
        )

        worksheet_path = (
            get_first_worksheet_path(
                archive
            )
        )

        print(
            f"[STREAM] File: {path.name}",
            flush=True,
        )

        print(
            "[STREAM] Requested columns: "
            + ", ".join(
                sorted(wanted_columns)
            ),
            flush=True,
        )

        print(
            f"[STREAM] Worksheet XML: "
            f"{worksheet_path}",
            flush=True,
        )

        with archive.open(
            worksheet_path
        ) as worksheet_source:

            row_tag = f"{{{MAIN_NS}}}row"
            sheet_data_tag = f"{{{MAIN_NS}}}sheetData"

            context = ET.iterparse(
                worksheet_source,
                events=("start", "end"),
            )

            # Track the sheetData element as it opens so processed
            # <row> children can be detached from it below. Without
            # this, iterparse's cleared row elements stay attached
            # as empty children of sheetData for the life of the
            # parse, growing with every row in the worksheet.
            sheet_data_elem = None

            for event, elem in context:

                if event == "start":
                    if elem.tag == sheet_data_tag:
                        sheet_data_elem = elem
                    continue

                if elem.tag != row_tag:
                    continue

                row_number_raw = (
                    elem.attrib.get("r")
                )

                try:
                    row_number = int(
                        row_number_raw
                    )
                except (
                    TypeError,
                    ValueError,
                ):
                    row_number = -1

                selected_values = {}

                for cell in elem.findall(
                    f"{{{MAIN_NS}}}c"
                ):
                    reference = (
                        cell.attrib.get(
                            "r",
                            "",
                        )
                    )

                    column = (
                        cell_reference_to_column(
                            reference
                        )
                    )

                    if (
                        column
                        not in wanted_columns
                    ):
                        continue

                    selected_values[column] = (
                        parse_cell_value(
                            cell,
                            shared_strings,
                        )
                    )

                yield StreamedRow(
                    row_number=row_number,
                    values=selected_values,
                )

                # Critical for streaming:
                # release XML row memory immediately, and detach
                # the now-empty row from sheetData so it doesn't
                # keep accumulating as the worksheet is parsed.
                elem.clear()

                if sheet_data_elem is not None:
                    sheet_data_elem.remove(elem)