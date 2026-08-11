# Yang-gumi release: 1.3.0
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WindowsBatchFormatTest(unittest.TestCase):
    def test_all_distributed_batch_files_use_cmd_safe_encoding_and_crlf(self):
        batches = sorted(ROOT.rglob("*.bat"))
        self.assertTrue(batches)
        for path in batches:
            with self.subTest(path=path.relative_to(ROOT)):
                payload = path.read_bytes()
                if any(byte >= 128 for byte in payload):
                    self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
                else:
                    payload.decode("ascii")
                self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()
