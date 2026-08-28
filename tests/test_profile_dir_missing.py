"""Unit tests for core.profiles.profile_dir_missing.

The helper powers the "honest status" endpoints: a DB row can say
"connected" while the durable Chromium profile was wiped (deploy without
the /app/profiles volume). Those endpoints use this to surface the split
without launching a browser.
"""
import os
import tempfile
import unittest


from core.profiles import profile_dir_missing


class ProfileDirMissingTests(unittest.TestCase):
    def test_falsy_path_is_missing(self):
        self.assertTrue(profile_dir_missing(None))
        self.assertTrue(profile_dir_missing(""))

    def test_nonexistent_path_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(profile_dir_missing(os.path.join(tmp, "does-not-exist")))

    def test_path_ending_in_file_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a-file")
            with open(path, "w") as fh:
                fh.write("x")
            self.assertTrue(profile_dir_missing(path))

    def test_empty_dir_is_missing(self):
        # A connected profile writes cookies/IndexedDB almost immediately, so
        # an empty dir under a "connected" row means the storage was wiped.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(profile_dir_missing(tmp))

    def test_dir_with_files_is_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "Cookies"), "w") as fh:
                fh.write("{}")
            self.assertFalse(profile_dir_missing(tmp))

    def test_unreadable_dir_degrades_to_missing_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            if os.name == "nt" or os.geteuid() == 0:
                self.skipTest("chmod 000 is ineffective for root/Windows")
            os.chmod(tmp, 0o000)
            try:
                self.assertTrue(profile_dir_missing(tmp))
            finally:
                os.chmod(tmp, 0o700)


if __name__ == "__main__":
    unittest.main()
