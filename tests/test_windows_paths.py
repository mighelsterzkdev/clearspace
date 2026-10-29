import ctypes
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from clearspace.windows import UnsafePathError, open_handle


def short_path(path):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetShortPathNameW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    api.GetShortPathNameW.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    return Path(buffer.value) if api.GetShortPathNameW(str(path), buffer, len(buffer)) else Path(path)


@unittest.skipUnless(os.name == "nt", "Windows paths")
class ShortNameTests(unittest.TestCase):
    """Windows may hand out 8.3 aliases (C:\\Users\\RUNNER~1) for long user names."""

    def setUp(self):
        temporary = TemporaryDirectory(prefix="clearspace-long-directory-name-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "long-directory-name").mkdir()
        (self.root / "long-directory-name" / "cache.tmp").write_bytes(b"x")
        self.short = short_path(self.root / "long-directory-name")
        if self.short == self.root / "long-directory-name":
            self.skipTest("8.3 names are disabled on this volume.")

    def test_short_alias_opens_the_same_entry(self):
        with open_handle(self.short, directory=True), open_handle(self.short / "cache.tmp"):
            pass

    def test_junction_behind_short_alias_is_still_rejected(self):
        import _winapi
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "kept.tmp").write_bytes(b"x")
        _winapi.CreateJunction(str(outside), str(self.root / "long-directory-name" / "link"))
        with self.assertRaises(UnsafePathError):
            open_handle(self.short / "link" / "kept.tmp").close()


if __name__ == "__main__":
    unittest.main()
