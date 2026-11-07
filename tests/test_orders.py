import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import Application
from payments import SandboxAPIError


class Orders(unittest.TestCase):
    def setUp(self):
        root = os.environ.get("OFFCUTFIT_TEST_TEMP")
        if not root:
            raise RuntimeError("Set OFFCUTFIT_TEST_TEMP to the marked task scratch.")
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.app = Application(self.temp.name)

    def tearDown(self):
        self.app.db.close()
        self.temp.cleanup()

    def quote(self):
        return self.app.quote({"pieces_mm":[500,500,350], "material":"pine"})

    def test_two_quotes_cannot_buy_same_remnant(self):
        q1,q2 = self.quote(),self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value = {"id":"SBOX1"}
            self.app.order(q1["quote_id"])
            with self.assertRaisesRegex(ValueError, "reserved"):
                self.app.order(q2["quote_id"])
            self.app.order(q1["quote_id"])
            self.assertEqual(paypal.return_value.create.call_count,1)

    def test_uncertain_order_does_not_release_stock(self):
        q1,q2 = self.quote(),self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.side_effect = TimeoutError("fixture")
            with self.assertRaises(TimeoutError):
                self.app.order(q1["quote_id"])
            with self.assertRaisesRegex(ValueError,"reserved"):
                self.app.order(q2["quote_id"])

    def test_wrong_capture_amount_is_not_recorded_as_payment(self):
        q = self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.return_value={"id":"SBOX1","status":"COMPLETED","purchase_units":[{"payments":{"captures":[{"status":"COMPLETED","amount":{"currency_code":"USD","value":"0.01"}}]}}]}
            with self.assertRaisesRegex(ValueError,"match"):
                self.app.capture(q["quote_id"])
            self.assertEqual(self.app.db.execute("select captured from quotes").fetchone()[0],0)

    def test_capture_once_marks_inventory_sold(self):
        q = self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.return_value={"id":"SBOX1","status":"COMPLETED","purchase_units":[{"payments":{"captures":[{"status":"COMPLETED","amount":{"currency_code":"USD","value":"9.70"}}]}}]}
            self.assertEqual(self.app.capture(q["quote_id"])["status"],"sandbox-captured")
            self.assertEqual(self.app.capture(q["quote_id"])["status"],"already-captured")
            self.assertEqual(paypal.return_value.capture.call_count,1)
            self.assertEqual(self.app.db.execute("select count(*) from stock where sold=1").fetchone()[0],2)

    @staticmethod
    def completed(order_id="SBOX1", value="9.70"):
        return {"id":order_id,"status":"COMPLETED","purchase_units":[{"payments":{"captures":[{"status":"COMPLETED","amount":{"currency_code":"USD","value":value}}]}}]}

    def test_checkout_cancellation_releases_stock_and_permanently_blocks_its_capture(self):
        q = self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value = {"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.get.return_value = {"id":"SBOX1", "status":"APPROVED"}
            self.assertEqual(self.app.cancel(q["quote_id"])["status"], "checkout-cancelled")
            self.assertEqual(self.app.db.execute("select count(*) from stock where quote_id is not null").fetchone()[0], 0)
            with self.assertRaisesRegex(ValueError,"closed"):
                self.app.capture(q["quote_id"])
            with self.assertRaisesRegex(ValueError,"closed"):
                self.app.order(q["quote_id"])
            self.assertTrue(self.quote()["feasible"])
            paypal.return_value.capture.assert_not_called()

    def test_unknown_create_can_be_closed_before_any_capture_was_attempted(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.side_effect=TimeoutError("fixture")
            with self.assertRaises(TimeoutError):
                self.app.order(q["quote_id"])
            self.assertEqual(self.app.cancel(q["quote_id"])["status"],"checkout-cancelled")
            self.assertEqual(self.app.db.execute("select count(*) from stock where quote_id is not null").fetchone()[0],0)
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"closed")

    def test_capture_timeout_keeps_stock_then_lookup_records_completed_capture(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=TimeoutError("fixture")
            with self.assertRaises(TimeoutError):
                self.app.capture(q["quote_id"])
            paypal.return_value.get.return_value={"id":"SBOX1","status":"APPROVED"}
            with self.assertRaisesRegex(ValueError,"capture was attempted"):
                self.app.cancel(q["quote_id"])
            self.assertEqual(self.app.db.execute("select count(*) from stock where quote_id is not null").fetchone()[0],2)
            paypal.return_value.get.return_value=self.completed()
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"sandbox-captured")
            self.assertEqual(self.app.db.execute("select count(*) from stock where sold=1").fetchone()[0],2)
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"already-captured")

    def test_uncertain_capture_retries_the_same_order_and_request_id(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=[TimeoutError("fixture"),self.completed()]
            with self.assertRaises(TimeoutError):self.app.capture(q["quote_id"])
            self.assertEqual(self.app.capture(q["quote_id"])["status"],"sandbox-captured")
            calls=paypal.return_value.capture.call_args_list
            self.assertEqual(calls[0],calls[1])

    def test_authoritative_void_releases_uncertain_capture_reservation(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=TimeoutError("fixture")
            with self.assertRaises(TimeoutError):self.app.capture(q["quote_id"])
            paypal.return_value.get.return_value={"id":"SBOX1","status":"VOIDED"}
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"sandbox-voided")
            self.assertEqual(self.app.db.execute("select count(*) from stock where quote_id is not null").fetchone()[0],0)

    def test_mismatched_lookup_amount_never_releases_or_sells_stock(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.get.return_value=self.completed(value="0.01")
            with self.assertRaisesRegex(ValueError,"match"):self.app.reconcile(q["quote_id"])
            self.assertEqual(self.app.db.execute("select count(*) from stock where sold=1").fetchone()[0],0)
            self.assertEqual(self.app.db.execute("select count(*) from stock where quote_id is not null").fetchone()[0],2)

    def test_unknown_create_is_not_reposted_after_bounded_retry_window(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.side_effect=TimeoutError("fixture")
            with self.assertRaises(TimeoutError):self.app.order(q["quote_id"])
            self.app.db.execute("update quotes set create_started_at=0 where id=?",(q["quote_id"],))
            self.app.db.commit()
            with self.assertRaisesRegex(ValueError,"retry window elapsed"):
                self.app.order(q["quote_id"])
            self.assertEqual(paypal.return_value.create.call_count,1)
            self.assertEqual(self.app.cancel(q["quote_id"])["status"],"checkout-cancelled")

    def test_uncertain_capture_is_not_reposted_after_bounded_retry_window(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=TimeoutError("fixture")
            with self.assertRaises(TimeoutError):self.app.capture(q["quote_id"])
            self.app.db.execute("update quotes set capture_started_at=0 where id=?",(q["quote_id"],))
            self.app.db.commit()
            with self.assertRaisesRegex(ValueError,"retry window elapsed"):
                self.app.capture(q["quote_id"])
            self.assertEqual(paypal.return_value.capture.call_count,1)
            paypal.return_value.get.return_value=self.completed()
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"sandbox-captured")

    def test_definite_unapproved_rejection_can_then_be_cancelled(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=SandboxAPIError(422,["ORDER_NOT_APPROVED"])
            with self.assertRaises(SandboxAPIError):self.app.capture(q["quote_id"])
            paypal.return_value.get.return_value={"id":"SBOX1","status":"CREATED"}
            self.assertEqual(self.app.cancel(q["quote_id"])["status"],"checkout-cancelled")

    def test_already_captured_error_is_never_treated_as_safe_rejection(self):
        q=self.quote()
        with patch("server.SandboxPayPal") as paypal:
            paypal.return_value.create.return_value={"id":"SBOX1"}
            self.app.order(q["quote_id"])
            paypal.return_value.capture.side_effect=SandboxAPIError(422,["ORDER_ALREADY_CAPTURED"])
            with self.assertRaises(SandboxAPIError):self.app.capture(q["quote_id"])
            paypal.return_value.get.return_value={"id":"SBOX1","status":"APPROVED"}
            with self.assertRaisesRegex(ValueError,"capture was attempted"):
                self.app.cancel(q["quote_id"])
            paypal.return_value.get.return_value=self.completed()
            self.assertEqual(self.app.reconcile(q["quote_id"])["status"],"sandbox-captured")


if __name__ == "__main__": unittest.main()
