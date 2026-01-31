from pathlib import Path
from typing import Annotated

import typer
from rich.progress import Progress

from msm.configs import Configs
from msm.musescore import Musescore
from msm.score import Score

app = typer.Typer()


def _validate_mscz_directory(path: Path | None):
    if path is None:
        typer.echo("mscz directory not set")
        raise typer.Exit(code=1)

    if not path.exists():
        typer.echo(f"mscz directory {path} does not exist")
        raise typer.Exit(code=1)


@app.command()
def normalize(
    path: Annotated[Path | None, typer.Argument(help="Path to a directory or a single .mscz file to normalize")] = None,
    profile: Annotated[str, typer.Argument(help="Configuration profile to use")] = "default",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    dryrun: Annotated[bool, typer.Option(help="Don't actually change filenames")] = False,
):
    configs = Configs()
    mscz_dir = path or Configs().local_mscz_directory()

    _validate_mscz_directory(mscz_dir)

    if mscz_dir.is_dir():
        mscz_paths = list(mscz_dir.glob("*.mscz"))
    elif mscz_dir.is_file() and mscz_dir.suffix == ".mscz":
        mscz_paths = [mscz_dir]
    else:
        typer.echo(f"{mscz_dir} is not a valid mscz file or directory")
        raise typer.Exit(code=1)

    musescore = Musescore(configs=configs)

    with Progress() as progress:
        task = progress.add_task("Normalizing scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            if verbose:
                progress.console.print(f"Normalizing {_path}")

            if not dryrun:
                Score(path=_path, read_metadata=True, musescore=musescore).normalize()

            progress.advance(task)


@app.command()
def upload():
    raise NotImplementedError()
