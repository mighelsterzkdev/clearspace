"""Frozen entry point; the self-check never opens a native window."""

import multiprocessing
import sys


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if sys.argv[1:] == ["--self-check"]:
        try:
            from clearspace.diagnostics import verify_portable
            verify_portable()
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0)
    from clearspace.gui import main
    raise SystemExit(main())
