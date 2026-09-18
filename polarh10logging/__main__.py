"""`python -m polarh10logging` opens the GUI; any arguments run the CLI."""

import sys


def main() -> int:
    if [a for a in sys.argv[1:] if a != "--fake"]:
        from .cli import main as cli_main
        return cli_main()
    from .gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
