import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest


class LocalHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = os.environ.get("OFFCUTFIT_TEST_TEMP")
        if not scratch:
            raise RuntimeError("Set OFFCUTFIT_TEST_TEMP to the marked task scratch.")
        cls.temp = tempfile.TemporaryDirectory(dir=scratch)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = os.environ.copy()
        for key in ("PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET", "OFFCUTFIT_MODEL_DIR"):
            env.pop(key, None)
        cls.process = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[1] / "app/server.py"), "--state-dir", cls.temp.name, "--port", str(cls.port)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            try:
                c=http.client.HTTPConnection("127.0.0.1",cls.port,timeout=1)
                c.request("GET","/api/config")
                result=c.getresponse();result.read();c.close()
                if result.status==200:return
            except OSError:
                pass
            if cls.process.poll() is not None:
                raise RuntimeError(cls.process.stderr.read().decode())
            time.sleep(0.1)
        cls.process.terminate()
        cls.process.wait(timeout=5)
        raise RuntimeError("Local app did not start.")

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.process.stderr.close()
        cls.temp.cleanup()

    def request(self, path, data=None, headers=None):
        c=http.client.HTTPConnection("127.0.0.1",self.port,timeout=5)
        if data is None:c.request("GET",path)
        else:c.request("POST",path,json.dumps(data),{"Content-Type":"application/json",**(headers or {})})
        r=c.getresponse();raw=r.read();c.close()
        return r.status,raw

    def test_page_and_config_are_served(self):
        status,raw=self.request("/")
        self.assertEqual(status,200);self.assertIn(b"OffcutFit",raw)
        status,raw=self.request("/api/config")
        self.assertEqual(json.loads(raw)["environment"],"sandbox")

    def test_quote_and_missing_credentials(self):
        status,raw=self.request("/api/quote",{"pieces_mm":[500,500,350],"material":"pine"})
        q=json.loads(raw)
        self.assertEqual(status,200);self.assertEqual(q["price_cents"],970)
        status,raw=self.request("/api/order",{"quote_id":q["quote_id"],"amount":"0.01"})
        self.assertEqual(status,400);self.assertIn(b"credentials",raw)

    def test_cross_origin_write_rejected(self):
        status,_=self.request("/api/quote",{"pieces_mm":[100],"material":"pine"},{"Origin":"https://unrelated.example"})
        self.assertEqual(status,403)


if __name__ == "__main__": unittest.main()
