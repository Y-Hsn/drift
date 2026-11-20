import sys

if '--net' in sys.argv:          # a short helper for the music core (no window)
    from drift.helper import main
    sys.exit(main(sys.argv[1:]))

from drift.app import main  # noqa: E402

sys.exit(main())
