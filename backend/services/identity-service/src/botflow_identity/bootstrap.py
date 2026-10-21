"""Explicit bootstrap entry point; no silent success before migrations exist."""


def main() -> None:
    raise SystemExit("Bootstrap is not implemented: add migrations and resource wiring.")


if __name__ == "__main__":
    main()
