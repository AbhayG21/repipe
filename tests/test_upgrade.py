"""upgrade: latest-release resolution (Releases API) + version gating. Offline."""

import json
import unittest
from unittest import mock

from repipe import cli
from repipe.errors import RepipeError

SAMPLE = json.dumps({
    "tag_name": "v1.6.0",
    "assets": [
        {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
        {"name": "repipe",
         "browser_download_url":
             "https://github.com/AbhayG21/repipe/releases/download/v1.6.0/repipe"},
    ],
})


class LatestRelease(unittest.TestCase):
    def test_parses_tag_and_repipe_asset(self):
        with mock.patch.object(cli, "download_text", return_value=SAMPLE):
            ver, url = cli._latest_release("AbhayG21/repipe")
        self.assertEqual(ver, "1.6.0")                       # 'v' stripped
        self.assertTrue(url.endswith("/download/v1.6.0/repipe"))

    def test_tag_without_v_prefix(self):
        body = json.dumps({"tag_name": "2.0.0", "assets": []})
        with mock.patch.object(cli, "download_text", return_value=body):
            ver, url = cli._latest_release("r")
        self.assertEqual(ver, "2.0.0")
        self.assertIsNone(url)                               # no repipe asset

    def test_empty_or_no_release(self):
        with mock.patch.object(cli, "download_text", return_value="{}"):
            self.assertEqual(cli._latest_release("r"), (None, None))

    def test_network_error_is_soft(self):
        with mock.patch.object(cli, "download_text",
                               side_effect=RepipeError("net down", 3)):
            self.assertEqual(cli._latest_release("r"), (None, None))


class VersionGating(unittest.TestCase):
    def test_ordering(self):
        self.assertGreater(cli._version_tuple("1.6.0"), cli._version_tuple("1.5.3"))
        self.assertGreater(cli._version_tuple("1.10.0"), cli._version_tuple("1.9.9"))
        self.assertFalse(cli._version_tuple("1.5.3") > cli._version_tuple("1.5.3"))


if __name__ == "__main__":
    unittest.main()
