import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from core import CATALOG, SemanticMatcher, plan_cut, paypal_payload
from payments import SandboxPayPal, BASE_URL


class CutTests(unittest.TestCase):
    def test_non_greedy_cut_list(self):
        p = plan_cut([500, 500, 350], "pine", 1500)
        self.assertTrue(p["feasible"])
        self.assertEqual(p["price_cents"], 970)
        self.assertEqual(sorted(v for b in p["bars"] for v in b["cuts_mm"]), [350, 500, 500])
        for b in p["bars"]:
            self.assertEqual(sum(b["cuts_mm"]) + b["tail_mm"] + b["kerf_loss_mm"], b["length_mm"])

    def test_too_small_budget_produces_no_order(self):
        p = plan_cut([500, 500, 350], "pine", 600)
        self.assertFalse(p["feasible"])
        with self.assertRaises(ValueError):
            paypal_payload(p)

    def test_search_exhaustion_is_not_reported_as_infeasibility(self):
        p = plan_cut([500, 500, 350], "pine", search_limit=1)
        self.assertFalse(p["feasible"])
        self.assertFalse(p["search_complete"])
        self.assertIn("infeasibility has not been established", p["reason"])
        nofit = plan_cut([3000], "pine")
        self.assertFalse(nofit["feasible"])
        self.assertTrue(nofit["search_complete"])

    def test_whole_bar_purchase_assigns_retained_tails_to_buyer(self):
        p = plan_cut([500, 500, 350], "pine")
        self.assertEqual(p["tail_owner"], "buyer")
        self.assertTrue(p["lowest_cost_proven"])
        self.assertGreater(p["retained_tail_mm"], 0)

    def test_kerf_not_squeezed_out(self):
        fixture = [{"id":"X", "material":"pine", "length_mm":1000,"width_mm":90,"thickness_mm":18,"price_cents":100}]
        self.assertFalse(plan_cut([500, 500], "pine", catalog=fixture)["feasible"])
        self.assertTrue(plan_cut([500, 497], "pine", catalog=fixture)["feasible"])
        self.assertFalse(plan_cut([999], "pine", catalog=fixture)["feasible"])

    def test_no_material_substitution(self):
        self.assertFalse(plan_cut([1400], "oak")["feasible"])
        self.assertTrue(all(b["material"] == "oak" for b in plan_cut([800], "oak")["bars"]))

    def test_malformed_input(self):
        for x in [[True], [0], [2.5], ["500"]]:
            with self.assertRaises(ValueError):
                plan_cut(x, "pine")

    def test_paypal_amount_bound_to_quote(self):
        p = plan_cut([500, 500, 350], "pine")
        d = paypal_payload(p)
        self.assertEqual(d["purchase_units"][0]["amount"], {"currency_code":"USD", "value":"9.70"})
        self.assertEqual(len(d["purchase_units"][0]["custom_id"]), 64)

    def test_sandbox_adapter_contract_and_idempotency_key(self):
        calls = []
        def transport(method, path, headers, payload):
            calls.append((method,path,headers,payload))
            return {"access_token":"fixture-token"} if path == "/v1/oauth2/token" else {"id":"FIXTUREORDER", "status":"CREATED"}
        paypal = SandboxPayPal("fixture-client", "fixture-secret", transport)
        result = paypal.create(paypal_payload(plan_cut([500], "pine")), "create-fixture")
        self.assertEqual(result["id"], "FIXTUREORDER")
        self.assertEqual(calls[-1][1], "/v2/checkout/orders")
        self.assertEqual(calls[-1][2]["PayPal-Request-Id"], "create-fixture")
        self.assertEqual(BASE_URL, "https://api-m.sandbox.paypal.com")
        paypal.get("FIXTUREORDER")
        self.assertEqual(calls[-1][0], "GET")
        self.assertIsNone(calls[-1][3])

    def test_local_model_returns_real_scores(self):
        d = SemanticMatcher().suggest("cheap painted craft")
        self.assertEqual(len(d["suggestions"]), 3)
        self.assertEqual(d["suggestions"][0]["material"], "pine")


if __name__ == "__main__":
    unittest.main()
