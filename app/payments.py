"""Only the PayPal sandbox API is supported. No live payment path exists."""
import base64
import json
import os
import urllib.error
import urllib.request

BASE_URL = "https://api-m.sandbox.paypal.com"


class SandboxAPIError(RuntimeError):
    def __init__(self, status, issues):
        self.status = status
        self.issues = frozenset(issues)
        super().__init__("The sandbox rejected the request; reconcile the existing order before retrying.")


class SandboxPayPal:
    def __init__(self, client_id=None, client_secret=None, transport=None):
        self.client_id = client_id or os.environ.get("PAYPAL_CLIENT_ID")
        self.client_secret = client_secret or os.environ.get("PAYPAL_CLIENT_SECRET")
        self.transport = transport or self._transport
        if not self.client_id or not self.client_secret:
            raise RuntimeError("PayPal sandbox credentials are not configured.")

    @staticmethod
    def _transport(method, path, headers, payload):
        data = None if payload is None else payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
        req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read())
                issues = [d.get("issue", "") for d in body.get("details", [])]
            except (ValueError, AttributeError):
                issues = []
            raise SandboxAPIError(exc.code, issues) from None

    def _token(self):
        encoded = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        return self.transport("POST", "/v1/oauth2/token", {"Authorization": "Basic " + encoded, "Content-Type": "application/x-www-form-urlencoded"}, "grant_type=client_credentials")["access_token"]

    def create(self, payload, request_id):
        return self.transport("POST", "/v2/checkout/orders", {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json", "PayPal-Request-Id": request_id}, payload)

    def capture(self, order_id, request_id):
        if not order_id.isalnum() or len(order_id) > 64:
            raise ValueError("Invalid order id.")
        return self.transport("POST", f"/v2/checkout/orders/{order_id}/capture", {"Authorization": "Bearer " + self._token(), "Content-Type": "application/json", "PayPal-Request-Id": request_id}, {})

    def get(self, order_id):
        if not order_id.isalnum() or len(order_id) > 64:
            raise ValueError("Invalid order id.")
        return self.transport("GET", f"/v2/checkout/orders/{order_id}", {"Authorization": "Bearer " + self._token()}, None)
