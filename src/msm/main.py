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


@app.callback()
def global_args(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Option(help="Path to a directory or a single .mscz file to run on")] = None,
    profile: Annotated[str, typer.Option(help="Configuration profile to use")] = "default",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    dryrun: Annotated[bool, typer.Option(help="Don't actually execute changes")] = False,
):
    """Global options and arguments"""
    configs = Configs(profile_name=profile)
    musescore = Musescore(configs=configs)

    ctx.ensure_object(dict)
    ctx.obj["dryrun"] = dryrun
    ctx.obj["musescore"] = musescore
    ctx.obj["path"] = path or configs.local_mscz_directory()
    ctx.obj["verbose"] = verbose


@app.command()
def normalize(ctx: typer.Context):
    dryrun = ctx.obj.get("dryrun", True)
    mscz_dir = ctx.obj.get("path", Configs().local_mscz_directory())
    musescore = ctx.obj.get("musescore", Musescore())
    verbose = ctx.obj.get("verbose", False)

    _validate_mscz_directory(mscz_dir)

    if mscz_dir.is_dir():
        mscz_paths = list(mscz_dir.glob("*.mscz"))
    elif mscz_dir.is_file() and mscz_dir.suffix == ".mscz":
        mscz_paths = [mscz_dir]
    else:
        typer.echo(f"{mscz_dir} is not a valid mscz file or directory")
        raise typer.Exit(code=1)

    with Progress() as progress:
        task = progress.add_task("Normalizing scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            if verbose or dryrun:
                progress.console.print(f"Normalizing {_path}")

            if not dryrun:
                Score(path=_path, read_metadata=True, musescore=musescore).normalize()

            progress.advance(task)


@app.command()
def upload():
    raise NotImplementedError()
