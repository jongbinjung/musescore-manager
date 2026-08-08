from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.progress import Progress

from msm.config import Configs
from msm.export import to_pngs
from msm.musescore import Musescore
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
    import boto3
    from botocore.client import Config

    context = _context(ctx)
    configs = context.configs
    dryrun = context.dryrun
    mscz_dir = context.path
    verbose = context.verbose

    mscz_dir = _require_mscz_path(mscz_dir)
    mscz_paths = _get_valid_mscz_paths(mscz_dir)

    if bucket is None:
        bucket = configs.mscz_bucket_name()

    if verbose:
        typer.echo(f"Using bucket: {bucket}")
        typer.echo(f"Using S3 endpoint: {configs.aws_endpoint_url_s3()}")

    # Create S3 service client
    svc = boto3.client(
        "s3",
        aws_access_key_id=configs.aws_access_key_id(),
        aws_secret_access_key=configs.aws_secret_access_key(),
        endpoint_url=configs.aws_endpoint_url_s3(),
        config=Config(s3={"addressing_style": "virtual"}),
    )

    paginator = svc.get_paginator("list_objects_v2")

    last_modified = {}
    for page in paginator.paginate(Bucket=bucket):
        if "Contents" in page:
            for obj in page["Contents"]:
                last_modified[obj["Key"]] = obj["LastModified"]

    with Progress() as progress:
        task = progress.add_task("Uploading scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            score = Score(path=_path, read_metadata=True)
            s3_key = score.normalized_name(with_key=True)

            upload = False
            if s3_key not in last_modified:
                upload = True
                if verbose or dryrun:
                    progress.console.print(f"{s3_key} does not exist on S3; uploading new version")
            else:
                if last_modified[s3_key] < score.source_modified_time_utc:
                    upload = True
                    if verbose or dryrun:
                        progress.console.print(f"{s3_key} is newer than S3 version; uploading new version")
                else:
                    upload = False
                    if verbose or dryrun:
                        progress.console.print(f"{s3_key} on S3 is up to date; skipping upload")

            if not dryrun and upload:
                svc.upload_file(
                    Filename=str(score.absolute_path),
                    Bucket=bucket,
                    Key=s3_key,
                )

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
