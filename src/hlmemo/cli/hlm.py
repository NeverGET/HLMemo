"""PLACEHOLDER — `hlm` Typer CLI (PHASE0-SPEC §5) is implemented in a later task."""

from __future__ import annotations

import typer

app = typer.Typer(help="HLMemo wrapper CLI (Phase 0 scaffold).", no_args_is_help=True)


@app.callback()
def _root() -> None:
    """HLMemo wrapper CLI (Phase 0 scaffold; real commands land in a later task)."""


@app.command()
def version() -> None:
    """Print the hlmemo package version."""
    from hlmemo import __version__

    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
