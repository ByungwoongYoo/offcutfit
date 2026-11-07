# OffcutFit

A local prototype that turns a short project description and cut list into a purchasable set of workshop remnants. It combines local sentence matching, bounded exact one-dimensional cutting, and a PayPal sandbox checkout adapter.

## Run

Use Python 3.12 and install `requirements.txt` in a project environment. Set `OFFCUTFIT_TEST_TEMP` to an explicitly chosen temporary directory before running the tests.

```powershell
python -m unittest discover -s tests -v
python app/server.py --state-dir C:/path/to/task-scratch --port 8713
```

Open `http://127.0.0.1:8713`. Describe the project, select a material, and enter comma-separated lengths. The synthetic default example is 500, 500, 350 mm; the selected pine remnants cost $9.70 in the fixture. This is an example price, not a supplier quote.

For MiniLM instead of the fitted TF-IDF baseline, download the pinned model with `python tools/fetch_model.py --output C:/path/to/model`, then set `OFFCUTFIT_MODEL_DIR` to that directory before starting the server. No inference API or paid credits are needed. The prototype has run this model on CPU.

## PayPal sandbox

Set `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET` to your own sandbox application's credentials. Keep the secret outside source control. The browser receives the public client ID; only the server uses the secret. The adapter has one fixed API base: `https://api-m.sandbox.paypal.com`.

The server creates an order from its stored quote and captures that same order after the buyer approves the PayPal button. It reserves individual remnants before order creation and marks them sold only after a matching complete capture. Its single-process lock serializes checkout changes; do not run multiple server processes against the same demonstration database.

Use **Check last checkout** after an unclear result. `POST /api/reconcile` looks up the existing order: a matching completed capture marks its entries sold, and an authoritative `VOIDED` result releases them and closes the quote. An unresolved result remains reserved. Repeating capture uses the same order and request key. Creation and capture retries stop after five hours rather than relying on PayPal's default six-hour idempotency retention indefinitely.

**Cancel last checkout** closes the local quote and frees its entries only before any capture could be outstanding, or after an authoritative void. The same closed quote cannot subsequently create/capture an order through this app. A capture timeout blocks cancellation until reconciliation; explicit pre-capture provider rejections are distinguished from timeouts or an already-captured response. This local action does not void a remote PayPal order or issue a refund. The prototype's assumption is that this application is the only capture initiator for its sandbox orders. It has no webhook, external capture processor or production refund workflow.

The current suite passes 27 tests with explicit payment fixtures and local HTTP checks. Authenticated Sandbox OAuth and order creation ran for USD9.70. After buyer approval, the sandbox capture completed and an official provider lookup confirmed a matching `COMPLETED` capture for USD9.70. The server marked remnants P1 and P3 sold, and a repeated local capture request returned `already-captured`. The PayPal SDK popup flow has not been verified. The cancellation/reconciliation failure routes have fixture evidence, rather than a real network failure experiment.

The server adds checkout-state columns to its existing local SQLite table. Preserve that database for reconciliation; do not reset it to clear an uncertain payment. An old database whose capture history is unknown needs merchant review, rather than assuming its prior operations were safe.

## Scope

The fixture represents one workshop, collection only, with a 90×18 mm cross-section, cuts along the existing length direction, and no joining or rotation. The example total includes cutting and has no additional demo tax or fees. Whole-bar purchase assigns the retained tails to the buyer; they do not return automatically to merchant stock and are not counted as waste saved. The solver distinguishes tails from saw loss. It supports at most 12 pieces, 16 eligible remnants and 100,000 visited search states. A state-limit interruption is returned as incomplete search, not proof that no plan exists; a complete plan found before interruption is labelled as not proven cheapest.

It does not certify material grade, load capacity, damage, treatment, or fitness for a particular build. A real merchant must qualify the inventory before listing it.

## Matching comparison

Run `python tools/compare_matching.py --model-dir C:/path/to/model` to compare local MiniLM and TF-IDF using the same three descriptions and twelve fixed, authored queries. The CPU run matched the author's intended card in 11/12 cases for each method. MiniLM recovered the layered-sheet paraphrase missed by TF-IDF, but misranked an explicit softwood query. No user study, independent generalization result or advantage over a user's direct material selection is claimed. The user always selects the actual material and enters the dimensional constraints.

## Technical references

- PayPal create-order request ID retention: https://developer.paypal.com/sdk/orders/v2/orders-create/
- PayPal order status definition: https://developer.paypal.com/api/orders/v2/definitions/order_status/
- Prior cutting-stock work, including usable leftovers, holding/setup costs and future-demand uncertainty: https://doi.org/10.1016/j.ejor.2025.03.014 . This prototype remains a one-period, one-dimensional whole-remnant purchase model, without a net-profit or waste-saving result.

## AI use

OffcutFit uses sentence-transformers/paraphrase-MiniLM-L3-v2 locally to rank material descriptions.

Codex was used for implementation, testing and documentation. ChatGPT reviewed the technical approach and claims. Claude edited the interface layout.
