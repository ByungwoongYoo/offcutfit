"""Run: python app/server.py --state-dir <task scratch> [--port 8713]."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from core import CATALOG, SemanticMatcher, plan_cut, paypal_payload, quote_hash
from payments import SandboxPayPal, SandboxAPIError


class Application:
    def __init__(self, state_dir):
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(state_dir) / "offcutfit.sqlite"), check_same_thread=False)
        self.db.execute("create table if not exists quotes (id text primary key, body text not null, order_id text, captured integer default 0)")
        self.db.execute("create table if not exists stock (id text primary key, quote_id text, sold integer default 0)")
        columns = {r[1] for r in self.db.execute("pragma table_info(quotes)")}
        if "closed" not in columns:
            self.db.execute("alter table quotes add column closed integer default 0")
        if "capture_attempted" not in columns:
            self.db.execute("alter table quotes add column capture_attempted integer default 0")
        if "create_started_at" not in columns:
            self.db.execute("alter table quotes add column create_started_at real")
        if "capture_started_at" not in columns:
            self.db.execute("alter table quotes add column capture_started_at real")
        self.db.executemany("insert or ignore into stock (id) values (?)", [(s["id"],) for s in CATALOG])
        self.db.commit()
        self.lock = threading.Lock()
        self.matcher = SemanticMatcher()

    def quote(self, data):
        with self.lock:
            available = {r[0] for r in self.db.execute("select id from stock where quote_id is null and sold=0")}
        plan = plan_cut(data["pieces_mm"], data["material"], data.get("max_cents", 100000), data.get("kerf_mm", 3), catalog=[s for s in CATALOG if s["id"] in available])
        if plan["feasible"]:
            qid = uuid.uuid4().hex
            with self.lock:
                self.db.execute("insert into quotes (id, body) values (?, ?)", (qid, json.dumps(plan)))
                self.db.commit()
            plan = {**plan, "quote_id": qid}
        return plan

    def order(self, qid):
        # Amounts come only from the stored server-generated quote.
        with self.lock:
            row = self.db.execute("select body, order_id, closed, create_started_at from quotes where id=?", (qid,)).fetchone()
            if not row:
                raise ValueError("Quote not found.")
            if row[2]:
                raise ValueError("This quote is closed. Generate a new plan.")
            if row[1]:
                return {"id": row[1], "status": "existing-sandbox-order"}
            if row[3] is not None and time.time() - row[3] >= 5 * 3600:
                raise ValueError("Creation retry window elapsed. Do not recreate an uncertain order; cancel this local quote or reconcile it through the sandbox merchant account.")
            paypal = SandboxPayPal()  # fail before reserving when credentials absent
            plan = json.loads(row[0])
            for bar in plan["bars"]:
                stock = self.db.execute("select quote_id, sold from stock where id=?", (bar["id"],)).fetchone()
                if stock[1] or stock[0] not in (None, qid):
                    raise ValueError("This remnant was reserved by another order. Please generate a new plan.")
            for bar in plan["bars"]:
                self.db.execute("update stock set quote_id=? where id=?", (qid, bar["id"]))
            self.db.execute("update quotes set create_started_at=coalesce(create_started_at, ?) where id=?", (time.time(), qid))
            self.db.commit()
            # An uncertain API result keeps the reservation. Retrying uses the same
            # PayPal idempotency key; stock is not sold twice after a timeout.
            result = paypal.create(paypal_payload(plan), "create-" + qid)
            if not result.get("id"):
                raise ValueError("PayPal did not return an order id.")
            self.db.execute("update quotes set order_id=? where id=?", (result["id"], qid))
            self.db.commit()
            return result

    def capture(self, qid):
        with self.lock:
            row = self.db.execute("select body, order_id, captured, closed, capture_started_at from quotes where id=?", (qid,)).fetchone()
            if not row or not row[1]:
                raise ValueError("Create the sandbox order first.")
            if row[3]:
                raise ValueError("This quote is closed; no capture will be attempted.")
            if row[2]:
                return {"status": "already-captured", "id": row[1]}
            if row[4] is not None and time.time() - row[4] >= 5 * 3600:
                raise ValueError("Capture retry window elapsed. Check the existing order; no new capture request was sent.")
            self.db.execute("update quotes set capture_attempted=1, capture_started_at=coalesce(capture_started_at, ?) where id=?", (time.time(), qid))
            self.db.commit()
            try:
                result = SandboxPayPal().capture(row[1], "capture-" + qid)
            except SandboxAPIError as exc:
                # These provider rejections are explicit pre-capture outcomes,
                # unlike a timeout, unknown error or already-captured response.
                safe = {"ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED", "INSTRUMENT_DECLINED"}
                if exc.status == 422 and exc.issues and exc.issues <= safe:
                    self.db.execute("update quotes set capture_attempted=0, capture_started_at=null where id=?", (qid,))
                    self.db.commit()
                raise
            return self._record_complete(qid, row[0], row[1], result)

    def _record_complete(self, qid, body, order_id, result):
        captures = [c for unit in result.get("purchase_units", []) for c in unit.get("payments", {}).get("captures", [])]
        expected = paypal_payload(json.loads(body))["purchase_units"][0]["amount"]
        if result.get("id") != order_id or result.get("status") != "COMPLETED" or len(captures) != 1 or captures[0].get("status") != "COMPLETED" or captures[0].get("amount") != expected:
            raise ValueError("Sandbox capture did not match this order's complete amount.")
        self.db.execute("update quotes set captured=1 where id=?", (qid,))
        self.db.execute("update stock set sold=1 where quote_id=?", (qid,))
        self.db.commit()
        return {"id": order_id, "status": "sandbox-captured", "amount": expected}

    def reconcile(self, qid):
        with self.lock:
            row = self.db.execute("select body, order_id, captured, closed from quotes where id=?", (qid,)).fetchone()
            if not row:
                raise ValueError("Quote not found.")
            if row[2]:
                return {"id": row[1], "status": "already-captured"}
            if row[3]:
                return {"status": "closed"}
            if not row[1]:
                return {"status": "no-order-id", "action": "Retry creation with the same quote, or cancel before any capture attempt."}
            result = SandboxPayPal().get(row[1])
            if result.get("id") != row[1]:
                raise ValueError("Sandbox lookup did not match this order.")
            if result.get("status") == "COMPLETED":
                return self._record_complete(qid, row[0], row[1], result)
            if result.get("status") == "VOIDED":
                return self._close(qid, "sandbox-voided")
            return {"id": row[1], "status": result.get("status", "unknown"), "action": "No payment completion recorded. Retry the same capture after approval; stock remains reserved until reconciliation or safe cancellation."}

    def _close(self, qid, status):
        self.db.execute("update quotes set closed=1 where id=?", (qid,))
        self.db.execute("update stock set quote_id=null where quote_id=? and sold=0", (qid,))
        self.db.commit()
        return {"status": status}

    def cancel(self, qid):
        # All captures in this single-process prototype go through capture().
        # Closing a quote permanently disables its future capture in this app.
        # This is a local checkout cancellation, not a PayPal void/refund call.
        with self.lock:
            row = self.db.execute("select body, order_id, captured, closed, capture_attempted from quotes where id=?", (qid,)).fetchone()
            if not row:
                raise ValueError("Quote not found.")
            if row[2]:
                raise ValueError("Completed payment cannot be cancelled here; no refund is performed.")
            if row[3]:
                return {"status": "closed"}
            if row[1]:
                result = SandboxPayPal().get(row[1])
                if result.get("id") != row[1]:
                    raise ValueError("Sandbox lookup did not match this order.")
                if result.get("status") == "COMPLETED":
                    return self._record_complete(qid, row[0], row[1], result)
                if result.get("status") == "VOIDED":
                    return self._close(qid, "sandbox-voided")
                if result.get("status") not in {"CREATED", "SAVED", "APPROVED", "PAYER_ACTION_REQUIRED"}:
                    raise ValueError("Order status is uncertain. Reconcile before releasing stock.")
            if row[4]:
                raise ValueError("A capture was attempted. Reconcile or retry the same capture; stock is not released on a timeout.")
            return self._close(qid, "checkout-cancelled")


def serve(state_dir, port):
    app = Application(state_dir)
    public = Path(__file__).resolve().parent.parent / "public"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def response(self, status, value):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/api/config":
                return self.response(200, {"client_id": os.environ.get("PAYPAL_CLIENT_ID", ""), "environment": "sandbox", "model": app.matcher.mode})
            if self.path != "/":
                return self.response(404, {"error": "Not found"})
            raw = (public / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            try:
                expected_origin = f"http://127.0.0.1:{port}"
                if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}") or self.headers.get("Origin", expected_origin) not in (expected_origin, f"http://localhost:{port}"):
                    return self.response(403, {"error": "Use the local app origin."})
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    return self.response(415, {"error": "JSON requests required."})
                if int(self.headers.get("Content-Length", "0")) > 20000:
                    return self.response(413, {"error": "Request too large"})
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if self.path == "/api/suggest":
                    result = app.matcher.suggest(data["query"])
                elif self.path == "/api/quote":
                    result = app.quote(data)
                elif self.path == "/api/order":
                    result = app.order(data["quote_id"])
                elif self.path == "/api/capture":
                    result = app.capture(data["quote_id"])
                elif self.path == "/api/reconcile":
                    result = app.reconcile(data["quote_id"])
                elif self.path == "/api/cancel":
                    result = app.cancel(data["quote_id"])
                else:
                    return self.response(404, {"error": "Not found"})
                self.response(200, result)
            except (ValueError, KeyError, RuntimeError) as exc:
                self.response(400, {"error": str(exc)})
            except Exception:
                self.response(502, {"error": "Sandbox service unavailable. No completed payment is recorded."})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"OffcutFit local prototype: http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--port", type=int, default=8713)
    args = parser.parse_args()
    serve(args.state_dir, args.port)
