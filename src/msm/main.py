from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.progress import Progress

from msm.config import Configs
from msm.export import to_pngs
from msm.musescore import Musescore
from msm.s3 import S3Store, SyncStatus, sync_file
from msm.score import Score

app = typer.Typer()


@dataclass
class AppContext:
    configs: Configs
    dryrun: bool
    path: Path | None
    verbose: bool


def _context(ctx: typer.Context) -> AppContext:
    if not isinstance(ctx.obj, AppContext):
        raise RuntimeError("Application context was not initialized")
    return ctx.obj


def _require_mscz_path(path: Path | None) -> Path:
    if path is None:
        typer.echo("mscz directory not set")
        raise typer.Exit(code=1)

    if not path.exists():
        typer.echo(f"mscz directory {path} does not exist")
        raise typer.Exit(code=1)

    return path


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
    ctx.obj = AppContext(
        configs=configs,
        dryrun=dryrun,
        path=path or configs.local_mscz_directory(),
        verbose=verbose,
    )


@app.command()
def normalize(ctx: typer.Context):
    context = _context(ctx)
    dryrun = context.dryrun
    mscz_dir = context.path
    verbose = context.verbose

    mscz_dir = _require_mscz_path(mscz_dir)
    mscz_paths = _get_valid_mscz_paths(mscz_dir)

    with Progress() as progress:
        task = progress.add_task("Normalizing scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            if verbose or dryrun:
                progress.console.print(f"Normalizing {_path}")

            if not dryrun:
                Score(path=_path, read_metadata=True).normalize()

            progress.advance(task)


@app.command()
def export_pngs(
    ctx: typer.Context,
    export_dir: Annotated[Path | None, typer.Option(help="Directory to export PNGs to")] = None,
):
    context = _context(ctx)
    configs = context.configs
    dryrun = context.dryrun
    mscz_dir = context.path
    verbose = context.verbose

    if export_dir is None:
        export_dir = configs.local_png_directory()
    if export_dir is None and not dryrun:
        typer.echo("PNG export directory not set")
        raise typer.Exit(code=1)

    mscz_dir = _require_mscz_path(mscz_dir)
    mscz_paths = _get_valid_mscz_paths(mscz_dir)
    musescore = None if dryrun else Musescore(configs.mscore_cmd())

    with Progress() as progress:
        task = progress.add_task("Exporting PNGs ...", total=len(mscz_paths))

        for _path in mscz_paths:
            if verbose or dryrun:
                progress.console.print(f"Exporting {_path}")

            if not dryrun:
                assert export_dir is not None
                score = Score(path=_path)
                assert musescore is not None
                results = to_pngs(score, musescore=musescore, key=None, base_dir=export_dir)
                if verbose:
                    for result in results:
                        progress.console.print(f"\t{result}")

            progress.advance(task)


@app.command()
def upload(ctx: typer.Context, bucket: str | None = None):
    context = _context(ctx)
    configs = context.configs
    dryrun = context.dryrun
    mscz_dir = context.path
    verbose = context.verbose

    mscz_dir = _require_mscz_path(mscz_dir)
    mscz_paths = _get_valid_mscz_paths(mscz_dir)

    if bucket is None:
        bucket = configs.mscz_bucket_name()
    if bucket is None:
        typer.echo("S3 bucket not set")
        raise typer.Exit(code=1)

    if verbose:
        typer.echo(f"Using bucket: {bucket}")
        typer.echo(f"Using S3 endpoint: {configs.aws_endpoint_url_s3()}")

    store = S3Store.connect(
        bucket=bucket,
        access_key_id=configs.aws_access_key_id(),
        secret_access_key=configs.aws_secret_access_key(),
        endpoint_url=configs.aws_endpoint_url_s3(),
    )

    with Progress() as progress:
        task = progress.add_task("Uploading scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            score = Score(path=_path, read_metadata=True)
            s3_key = score.normalized_name(with_key=True)
            result = sync_file(store, score.absolute_path, s3_key, dryrun=dryrun)

            if verbose or dryrun:
                match result.status:
                    case SyncStatus.CREATED:
                        action = "would upload" if dryrun else "uploading"
                        progress.console.print(f"{s3_key} does not exist on S3; {action} new version")
                    case SyncStatus.UPDATED:
                        action = "would upload" if dryrun else "uploading"
                        progress.console.print(f"{s3_key} differs from S3; {action} new version")
                    case SyncStatus.UNCHANGED:
                        progress.console.print(f"{s3_key} on S3 is up to date; skipping upload")
                    case SyncStatus.REMOTE_NEWER:
                        progress.console.print(f"{s3_key} on S3 is newer; skipping upload")
                    case SyncStatus.CONFLICT:
                        progress.console.print(f"{s3_key} conflicts with S3 at the same timestamp; skipping upload")

            progress.advance(task)


def _get_valid_mscz_paths(path: Path) -> list[Path]:
    if path.is_dir():
        mscz_paths = list(path.glob("*.mscz"))
    elif path.is_file() and path.suffix == ".mscz":
        mscz_paths = [path]
    else:
        typer.echo(f"{path} is not a valid mscz file or directory")
        raise typer.Exit(code=1)

    return mscz_paths
