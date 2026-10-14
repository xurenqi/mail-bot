import base64
import json
from pathlib import Path
import tempfile
import unittest

from scripts.request_crypto import (
    RequestCryptoError,
    decrypt_envelope,
    encrypt_request,
    generate_keypair,
)


class RequestCryptoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.private = cls.root / "private.pem"
        cls.public = cls.root / "public.pem"
        generate_keypair(cls.private, cls.public)
        cls.private_pem = cls.private.read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def request(self):
        return json.dumps({
            "version": 1,
            "request_id": "01234567-89ab-4cde-8f01-23456789abcd",
            "action": "发送自定义邮件",
            "subject": "私密中文主题",
            "body": "私密中文正文",
            "timing": "now",
        }, ensure_ascii=False).encode("utf-8")

    def test_hybrid_encryption_round_trip_hides_plaintext(self):
        plaintext = self.request()
        document = encrypt_request(self.public, plaintext)
        encoded = json.dumps(document, ensure_ascii=False)
        self.assertEqual(document["version"], 2)
        self.assertEqual(document["request_id"], "01234567-89ab-4cde-8f01-23456789abcd")
        self.assertNotIn("私密中文主题", encoded)
        self.assertNotIn("私密中文正文", encoded)
        self.assertEqual(decrypt_envelope(document["encrypted"], self.private_pem), plaintext)

    def test_modified_ciphertext_fails_authentication(self):
        document = encrypt_request(self.public, self.request())
        envelope = document["encrypted"]
        ciphertext = bytearray(base64.b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
        with self.assertRaises(RequestCryptoError):
            decrypt_envelope(envelope, self.private_pem)

    def test_untrusted_or_malformed_envelope_fails_closed(self):
        document = encrypt_request(self.public, self.request())
        with self.assertRaises(RequestCryptoError):
            decrypt_envelope({**document["encrypted"], "extra": "field"}, self.private_pem)
        with self.assertRaises(RequestCryptoError):
            decrypt_envelope(document["encrypted"], "")

    def test_encrypt_rejects_noncanonical_request_id(self):
        payload = json.loads(self.request().decode("utf-8"))
        payload["request_id"] = payload["request_id"].upper()
        with self.assertRaises(RequestCryptoError):
            encrypt_request(self.public, json.dumps(payload).encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
