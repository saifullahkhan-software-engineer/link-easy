"""Validation tests for WhatsApp multi-filter configuration payloads."""
import unittest

from pydantic import ValidationError

from schemas.whatsapp import WhatsAppGroupSelectRequest, WhatsAppScanFilterCreate


class WhatsAppSchemaTests(unittest.TestCase):
    def test_one_monitored_group_is_valid(self):
        payload = WhatsAppGroupSelectRequest(
            filter_id=4,
            monitored_group_names=["Lahore Tech Jobs"],
            monitored_group_ids=["group-1"],
            forward_group_name="Matched Jobs",
        )

        self.assertEqual(payload.monitored_group_names, ["Lahore Tech Jobs"])

    def test_three_monitored_groups_remain_valid(self):
        payload = WhatsAppGroupSelectRequest(
            monitored_group_names=["One", "Two", "Three"],
            monitored_group_ids=["1", "2", "3"],
            forward_group_name="Matched Jobs",
        )

        self.assertEqual(len(payload.monitored_group_names), 3)

    def test_zero_or_more_than_three_monitored_groups_are_invalid(self):
        for count in (0, 4):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                WhatsAppGroupSelectRequest(
                    monitored_group_names=[f"Group {index}" for index in range(count)],
                    monitored_group_ids=[str(index) for index in range(count)],
                    forward_group_name="Matched Jobs",
                )

    def test_group_names_and_ids_must_line_up(self):
        with self.assertRaises(ValidationError):
            WhatsAppGroupSelectRequest(
                monitored_group_names=["One", "Two"],
                monitored_group_ids=["1"],
                forward_group_name="Matched Jobs",
            )

    def test_duplicate_group_names_are_invalid(self):
        with self.assertRaises(ValidationError):
            WhatsAppGroupSelectRequest(
                monitored_group_names=["Jobs", " jobs "],
                monitored_group_ids=["1", "2"],
                forward_group_name="Matched Jobs",
            )

    def test_duplicate_nonempty_group_ids_are_invalid(self):
        with self.assertRaises(ValidationError):
            WhatsAppGroupSelectRequest(
                monitored_group_names=["One", "Two"],
                monitored_group_ids=["same-id", "same-id"],
                forward_group_name="Matched Jobs",
            )

    def test_latest_message_limit_is_bounded(self):
        self.assertEqual(
            WhatsAppScanFilterCreate(name="Jobs", latest_messages_limit=1).latest_messages_limit,
            1,
        )
        self.assertEqual(
            WhatsAppScanFilterCreate(name="Jobs", latest_messages_limit=100).latest_messages_limit,
            100,
        )
        for value in (0, 101):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                WhatsAppScanFilterCreate(name="Jobs", latest_messages_limit=value)


if __name__ == "__main__":
    unittest.main()
