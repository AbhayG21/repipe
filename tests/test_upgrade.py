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


class PassiveUpdateCheck(unittest.TestCase):
    """_update_available: throttled, cached, best-effort welcome-screen check."""

    def _run(self, cache, latest_ret, now=1_000_000.0, version="2.2.0", env=None):
        writes = []
        with mock.patch.object(cli, "__version__", version), \
                mock.patch.dict(cli.os.environ, env or {}, clear=False), \
                mock.patch.object(cli, "_read_update_cache", return_value=cache), \
                mock.patch.object(cli, "_write_update_cache",
                                  side_effect=lambda v: writes.append(v)), \
                mock.patch.object(cli, "_latest_release",
                                  return_value=latest_ret) as fetch:
            result = cli._update_available(now=now)
        return result, fetch, writes

    def test_reports_newer_when_stale_and_release_ahead(self):
        # cache is old (checked_at 0) → probe; API says 2.3.0 → newer than 2.2.0
        result, fetch, writes = self._run((0.0, ""), ("2.3.0", None))
        self.assertEqual(result, "2.3.0")
        fetch.assert_called_once()
        self.assertEqual(writes, ["2.3.0"])            # cache refreshed

    def test_uses_cache_within_interval_without_network(self):
        # checked 1s ago → within 24h → trust the cached 'latest', no API call
        result, fetch, _ = self._run((999_999.0, "2.3.0"), ("9.9.9", None))
        self.assertEqual(result, "2.3.0")
        fetch.assert_not_called()

    def test_none_when_up_to_date(self):
        result, _, _ = self._run((999_999.0, "2.2.0"), ("2.2.0", None))
        self.assertIsNone(result)

    def test_network_failure_preserves_cached_latest(self):
        # stale cache with a good 'latest', but the probe fails → keep the old one
        result, fetch, writes = self._run((0.0, "2.3.0"), (None, None))
        self.assertEqual(result, "2.3.0")
        fetch.assert_called_once()
        self.assertEqual(writes, ["2.3.0"])            # preserved, attempt stamped

    def test_opt_out_env_is_silent(self):
        result, fetch, _ = self._run((0.0, ""), ("9.9.9", None),
                                     env={"REPIPE_NO_UPDATE_CHECK": "1"})
        self.assertIsNone(result)
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
