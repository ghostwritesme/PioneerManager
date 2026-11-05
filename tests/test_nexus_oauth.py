import io
import json
import os
from contextlib import contextmanager
import shutil
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from unittest import mock

import nexus

TEST_TEMP_ROOT = Path(__file__).resolve().parent / ".tmp"


class NexusOAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TEST_TEMP_ROOT.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_TEMP_ROOT, ignore_errors=True)

    @contextmanager
    def temporary_directory(self):
        # tempfile.mkdtemp() creates mode-0700 directories. Some Windows sandbox
        # runners translate that mode into an ACL that even the test cannot use.
        path = TEST_TEMP_ROOT / uuid.uuid4().hex
        path.mkdir(mode=0o777)
        try:
            yield str(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is required")
    def test_tokens_are_encrypted_and_reloadable(self):
        with self.temporary_directory() as directory:
            path = Path(directory) / "nexus_auth.dat"
            auth = nexus.NexusOAuth(path)
            auth._save({"access_token": "test-access-token", "refresh_token": "test-refresh-token",
                        "expires_in": 3600})
            self.assertNotIn(b"test-access-token", path.read_bytes())
            reloaded = nexus.NexusOAuth(path)
            self.assertTrue(reloaded.connected)
            self.assertEqual(reloaded.access_token(), "test-access-token")
            reloaded.sign_out()
            self.assertFalse(path.exists())

    def test_refresh_uses_refresh_grant_and_replaces_tokens(self):
        with self.temporary_directory() as directory:
            auth = nexus.NexusOAuth(Path(directory) / "nexus_auth.dat")
            with mock.patch.object(nexus, "_protect", side_effect=lambda value: value):
                auth._save({"access_token": "old", "refresh_token": "refresh", "expires_in": 0})
                with mock.patch.object(nexus, "OAUTH_CLIENT_ID", "test-client"), \
                     mock.patch.object(auth, "_post_token", return_value={
                         "access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600,
                     }) as post:
                    self.assertEqual(auth.access_token(), "new")
                self.assertEqual(post.call_args.args[0]["grant_type"], "refresh_token")
                self.assertEqual(post.call_args.args[0]["client_id"], "test-client")
                self.assertEqual(post.call_args.args[0]["refresh_token"], "refresh")

    def test_api_uses_bearer_token_and_refreshes_once_on_401(self):
        seen = []

        def token_provider(force_refresh=False):
            seen.append(force_refresh)
            return "new" if force_refresh else "old"

        def urlopen(request, timeout):
            headers = dict(request.header_items())
            self.assertNotIn("apikey", headers)
            self.assertEqual(request.get_header("Application-name"), "Pioneer Manager")
            self.assertEqual(request.get_header("Application-version"), "1.0.0")
            if request.get_header("Authorization") == "Bearer old":
                raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", None, io.BytesIO())
            self.assertEqual(request.get_header("Authorization"), "Bearer new")
            response = io.BytesIO(json.dumps({"name": "Tester"}).encode())
            response.headers = {}
            return response

        with mock.patch.object(nexus.urllib.request, "urlopen", side_effect=urlopen):
            self.assertEqual(nexus.NexusClient(token_provider).validate()["name"], "Tester")
        self.assertEqual(seen, [False, True])

    def test_validate_uses_userinfo_endpoint_and_parses_roles(self):
        client = nexus.NexusClient(lambda: "test-token")
        for roles, expected_premium in [
            (["member"], False),
            (["member", "premium"], True),
            (["lifetimepremium"], True),
            ([], False),
        ]:
            def urlopen(request, timeout):
                self.assertEqual(request.full_url, nexus.OAUTH_USERINFO)
                self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
                self.assertEqual(request.get_header("Application-name"), "Pioneer Manager")
                response = io.BytesIO(json.dumps({
                    "sub": 99999,
                    "name": "NexusUser",
                    "membership_roles": roles,
                }).encode())
                response.headers = {}
                return response

            with mock.patch.object(nexus.urllib.request, "urlopen", side_effect=urlopen):
                result = client.validate()
                self.assertEqual(result["name"], "NexusUser")
                self.assertEqual(result["user_id"], 99999)
                self.assertEqual(result["is_premium"], expected_premium)

    def test_validate_extracts_user_from_jwt_token_when_userinfo_only_has_sub(self):
        payload = {
            "sub": "121317093",
            "user": {
                "id": 121317093,
                "username": "ghostwritesme",
                "membership_roles": ["member"],
            },
        }
        raw_b64 = nexus.base64.urlsafe_b64encode(nexus.json.dumps(payload).encode()).decode().rstrip("=")
        fake_jwt = f"header.{raw_b64}.sig"
        client = nexus.NexusClient(lambda: fake_jwt)

        def urlopen(request, timeout):
            response = io.BytesIO(nexus.json.dumps({"sub": "121317093"}).encode())
            response.headers = {}
            return response

        with mock.patch.object(nexus.urllib.request, "urlopen", side_effect=urlopen):
            result = client.validate()
            self.assertEqual(result["name"], "ghostwritesme")
            self.assertEqual(result["user_id"], "121317093")
            self.assertFalse(result["is_premium"])

    def test_token_exchange_uses_required_application_headers(self):
        def urlopen(request, timeout):
            self.assertEqual(request.get_header("Application-name"), "Pioneer Manager")
            self.assertEqual(request.get_header("Application-version"), "1.0.0")
            self.assertEqual(request.get_header("Content-type"),
                             "application/x-www-form-urlencoded")
            response = io.BytesIO(json.dumps({
                "access_token": "access", "refresh_token": "refresh", "expires_in": 3600,
            }).encode())
            response.headers = {}
            return response

        with mock.patch.object(nexus.urllib.request, "urlopen", side_effect=urlopen):
            result = nexus.NexusOAuth._post_token({"grant_type": "authorization_code"})
        self.assertEqual(result["access_token"], "access")

    def test_connect_uses_pkce_state_and_loopback_callback(self):
        with self.temporary_directory() as directory:
            auth = nexus.NexusOAuth(Path(directory) / "nexus_auth.dat")
            browser_threads = []
            authorize = {}

            def open_browser(url):
                authorize.update(urllib.parse.parse_qs(urllib.parse.urlparse(url).query))

                def callback():
                    params = urllib.parse.urlencode({"state": authorize["state"][0], "code": "once"})
                    with urllib.request.urlopen(f"{nexus.OAUTH_REDIRECT_URI}?{params}", timeout=5) as response:
                        self.assertEqual(response.status, 200)

                thread = threading.Thread(target=callback)
                thread.start()
                browser_threads.append(thread)
                return True

            def exchange(fields):
                self.assertEqual(fields["code"], "once")
                self.assertEqual(fields["grant_type"], "authorization_code")
                self.assertEqual(fields["redirect_uri"], nexus.OAUTH_REDIRECT_URI)
                self.assertNotIn("scope", fields)
                self.assertEqual(authorize["code_challenge_method"], ["S256"])
                self.assertEqual(authorize["client_id"], ["test-client"])
                self.assertGreaterEqual(len(authorize["state"][0]), 32)
                expected = nexus.base64.urlsafe_b64encode(
                    nexus.hashlib.sha256(fields["code_verifier"].encode("ascii")).digest()
                ).rstrip(b"=").decode("ascii")
                self.assertEqual(authorize["code_challenge"], [expected])
                return {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}

            with mock.patch.object(nexus, "OAUTH_CLIENT_ID", "test-client"), \
                 mock.patch.object(nexus.webbrowser, "open", side_effect=open_browser), \
                 mock.patch.object(auth, "_post_token", side_effect=exchange), \
                 mock.patch.object(nexus, "_protect", side_effect=lambda value: value), \
                 mock.patch.object(nexus.NexusClient, "validate", return_value={"name": "Tester"}):
                self.assertEqual(auth.connect()["name"], "Tester")
            for thread in browser_threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

    def test_unreadable_saved_tokens_do_not_prevent_startup(self):
        with self.temporary_directory() as directory:
            path = Path(directory) / "nexus_auth.dat"
            path.write_bytes(b"not encrypted token data")
            with mock.patch.object(nexus, "_unprotect", side_effect=ValueError("bad token file")):
                auth = nexus.NexusOAuth(path)
            self.assertFalse(auth.connected)
            self.assertIn("could not be read", auth.load_error)
            self.assertTrue(path.exists())

    def test_missing_client_id_blocks_login_before_opening_browser(self):
        with self.temporary_directory() as directory:
            auth = nexus.NexusOAuth(Path(directory) / "nexus_auth.dat")
            with mock.patch.object(nexus, "OAUTH_CLIENT_ID", ""), \
                 mock.patch.object(nexus.webbrowser, "open") as open_browser:
                with self.assertRaisesRegex(nexus.NexusApiError, "pending registration"):
                    auth.connect(timeout=0)
            open_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
