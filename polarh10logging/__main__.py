"""`python -m polarh10logging` opens the app window; any arguments run the CLI."""

import sys


def main() -> int:
    if [a for a in sys.argv[1:] if a != "--fake"]:
        from .cli import main as cli_main
        return cli_main()
    from .webui import main as ui_main
    ui_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
