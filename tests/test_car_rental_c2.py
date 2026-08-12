import unittest
from pathlib import Path

from src.processors.car_rental_c2 import process_car_rental_c2

FIXTURE = Path(__file__).parent / "fixtures" / "sample_c2.xlsx"


class CarRentalC2LookupTests(unittest.TestCase):
    def test_matches_return_expected_fields(self):
        result = process_car_rental_c2(
            file_paths=[FIXTURE],
            lookup_keys=["SYN-1002", "SYN-1003"],
        )

        matches_by_item = {
            match["Item"]: match for match in result["matches"]
        }

        self.assertEqual(result["matched_count"], 2)
        self.assertEqual(
            matches_by_item["SYN-1002"]["VAR"], "V2"
        )
        self.assertEqual(
            matches_by_item["SYN-1002"]["Order Status"], "Shipped"
        )
        self.assertEqual(
            matches_by_item["SYN-1002"]["Est Total Price"], 250
        )
        self.assertEqual(
            matches_by_item["SYN-1003"]["VAR"], "V3"
        )

    def test_duplicate_item_within_file_first_occurrence_wins(self):
        # SYN-1001 appears twice in the fixture (rows 2 and 4).
        # Row 2 is the first occurrence and must be the one returned,
        # with row 4's values ("ShouldBeIgnored" / 999.99) discarded.
        result = process_car_rental_c2(
            file_paths=[FIXTURE],
            lookup_keys=["SYN-1001"],
        )

        self.assertEqual(result["matched_count"], 1)

        match = result["matches"][0]
        self.assertEqual(match["_source_row"], 2)
        self.assertEqual(match["VAR"], "V1")
        self.assertEqual(match["Order Status"], "Ordered")
        self.assertEqual(match["Est Total Price"], 100.5)

    def test_not_found_keys_are_reported(self):
        result = process_car_rental_c2(
            file_paths=[FIXTURE],
            lookup_keys=["SYN-1001", "SYN-9999"],
        )

        self.assertEqual(result["requested_count"], 2)
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["not_found"], ["SYN-9999"])


if __name__ == "__main__":
    unittest.main()
