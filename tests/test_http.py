"""HTTP error mapping — the messages users actually see on 4xx (pure, no network)."""

import unittest
import urllib.error

from repipe.errors import RepipeError
from repipe.http import _raise_http


def _http_error(url, code):
    # fp=None keeps this a pure object (no socket); .filename carries the url,
    # which is what _raise_http reads for the host + the URL line.
    return urllib.error.HTTPError(url, code, "err", {}, None)


class Raise404(unittest.TestCase):
    def test_bitbucket_hint_and_url(self):
        e = _http_error(
            "https://api.bitbucket.org/2.0/repositories/acme/widget/pipelines/", 404
        )
        with self.assertRaises(RepipeError) as cm:
            _raise_http(e)
        msg = str(cm.exception)
        self.assertIn("not found (404)", msg)
        self.assertIn("branch/ref may not be pushed", msg)
        self.assertIn("acme/widget/pipelines/", msg)   # URL surfaced for debugging

    def test_github_hint_mentions_private_repo(self):
        e = _http_error(
            "https://api.github.com/repos/acme/widget/actions/workflows/deploy.yml/dispatches",
            404,
        )
        with self.assertRaises(RepipeError) as cm:
            _raise_http(e)
        self.assertIn("private repo", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
