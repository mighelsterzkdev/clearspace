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
    if sys.argv[1:2] == ["--finish-update"]:
        # Run from a staged release after the previous copy exits; installs it, then reopens the app.
        from clearspace.updates import finish
        raise SystemExit(finish(sys.argv[2:], "clearspace.exe"))
    from clearspace.gui import main
    raise SystemExit(main())
