import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import typer
from rich.progress import Progress

from msm.config import Configs
from msm.export import png_export_status, to_pngs
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


def _create_drive_session(creds_path: Path, folder_id: str):
    from msm.gdrive import DriveSession

    session = DriveSession.from_credentials(creds_path, folder_id)
    session.validate_folder()
    return session


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
    scores_to_normalize = len(mscz_paths)

    with Progress() as progress:
        task = progress.add_task("Normalizing scores...", total=len(mscz_paths))

        for _path in mscz_paths:
            if verbose:
                progress.console.print(f"Normalizing {_path}")

            if not dryrun:
                Score(path=_path, read_metadata=True).normalize()

            progress.advance(task)

    if dryrun:
        progress.console.print(f"Would normalize {scores_to_normalize} scores.")


@app.command()
def export_pngs(
    ctx: typer.Context,
    export_dir: Annotated[Path | None, typer.Option(help="Directory to export PNGs to")] = None,
    jobs: Annotated[int, typer.Option(min=1, help="Maximum concurrent exports")] = 4,
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
    musescore = Musescore(configs.mscore_cmd())
    scores_to_export = 0
    pngs_to_export = 0

    with Progress() as progress:
        task = progress.add_task("Exporting PNGs ...", total=len(mscz_paths))

        if dryrun:
            for _path in mscz_paths:
                if verbose:
                    progress.console.print(f"Exporting {_path}")
                score = Score(path=_path)
                if export_dir is None:
                    pages = musescore.metadata(score).pages
                    if pages is None or pages < 1:
                        raise RuntimeError("MuseScore metadata did not include a positive page count")
                    scores_to_export += 1
                    pngs_to_export += pages
                else:
                    target_path = export_dir / score.normalized_name(with_key=True, suffix="png")
                    expected, _, up_to_date = png_export_status(score, target_path)
                    if not up_to_date:
                        pages = musescore.metadata(score).pages
                        if pages is None or pages < 1:
                            raise RuntimeError("MuseScore metadata did not include a positive page count")
                        scores_to_export += 1
                        pngs_to_export += pages
                progress.advance(task)
        else:
            assert export_dir is not None
            # Plan destination identities before starting MuseScore processes.
            planned = [(path, Score(path)) for path in mscz_paths]
            destinations: dict[str, list[Path]] = {}
            for path, planned_score in planned:
                target = planned_score.normalized_name(with_key=True, suffix="png")
                destinations.setdefault(unicodedata.normalize("NFC", target), []).append(path)
            collisions = {target: paths for target, paths in destinations.items() if len(paths) > 1}
            if collisions:
                for target, paths in collisions.items():
                    typer.echo(f"Duplicate PNG target {target} for: {', '.join(str(path) for path in paths)}")
                raise typer.Exit(code=1)

            results_by_index: list[list[Path] | Exception | None] = [None] * len(planned)

            def run_export(item: tuple[Path, Score]) -> list[Path]:
                _, planned_score = item
                return to_pngs(
                    planned_score,
                    musescore=Musescore(configs.mscore_cmd()),
                    key=None,
                    base_dir=export_dir,
                )

            with ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {executor.submit(run_export, item): index for index, item in enumerate(planned)}
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results_by_index[index] = future.result()
                    except Exception as error:
                        results_by_index[index] = error

            failures = 0
            for index, result in enumerate(results_by_index):
                progress.advance(task)
                if isinstance(result, Exception):
                    progress.console.print(f"Failed to export {planned[index][0].name}: {result}")
                    failures += 1
                    continue
                if verbose and result:
                    for output in result:
                        progress.console.print(f"\t{output}")
            if failures:
                raise typer.Exit(code=1)

    if dryrun:
        progress.console.print(f"Would export {scores_to_export} scores and {pngs_to_export} PNGs.")


@app.command()
def sync_pngs(ctx: typer.Context):
    context = _context(ctx)
    configs = context.configs
    dryrun = context.dryrun
    verbose = context.verbose

    folder_id = configs.google_drive_folder_id()
    creds_path = configs.google_app_credentials_json_path()

    if not folder_id:
        typer.echo("GOOGLE_DRIVE_FOLDER_ID is not configured")
        raise typer.Exit(code=1)
    if creds_path is None:
        typer.echo("GOOGLE_APP_CREDENTIALS_JSON_PATH is not configured")
        raise typer.Exit(code=1)

    png_dir = configs.local_png_directory()
    if png_dir is None:
        typer.echo("LOCAL_PNG_DIRECTORY is not configured")
        raise typer.Exit(code=1)

    if not png_dir.is_dir():
        typer.echo(f"PNG directory {png_dir} does not exist")
        raise typer.Exit(code=1)

    png_files = sorted(path for path in png_dir.iterdir() if path.is_file() and path.suffix.lower() == ".png")
    if not png_files:
        typer.echo("No PNG files found")
        if dryrun:
            typer.echo("Would sync 0 PNGs.")
        return

    try:
        session = _create_drive_session(creds_path, folder_id)
    except Exception as error:
        typer.echo(f"Failed to connect to Google Drive: {error}")
        raise typer.Exit(code=1)

    failures = 0
    summary = {status: 0 for status in ("created", "updated", "skipped", "would_create", "would_update", "conflict")}

    with Progress() as progress:
        task = progress.add_task("Syncing PNGs...", total=len(png_files))

        for filepath in png_files:
            if verbose:
                progress.console.print(f"Syncing {filepath.name}")

            try:
                result = session.sync_file(filepath, dryrun=dryrun)
                labels = {
                    "created": "Created",
                    "updated": "Updated",
                    "skipped": "Up to date; skipped",
                    "would_create": "Would create",
                    "would_update": "Would update",
                    "conflict": "Conflict",
                }
                detail = f": {result.error}" if result.error else ""
                if verbose or not dryrun:
                    progress.console.print(f"  {labels[result.status.value]} {filepath.name}{detail}")
                summary[result.status.value] += 1
                if result.status.value == "conflict":
                    failures += 1
            except Exception as e:
                progress.console.print(f"  Failed to upload {filepath.name}: {e}")
                failures += 1

            progress.advance(task)

    if dryrun:
        progress.console.print(
            "Would sync: "
            f"{summary['would_create']} create, "
            f"{summary['would_update']} update, "
            f"{summary['skipped']} skipped, "
            f"{summary['conflict']} conflicts."
        )

    if failures:
        raise typer.Exit(code=1)


@app.command()
def upload(
    ctx: typer.Context,
    bucket: str | None = None,
    jobs: Annotated[int, typer.Option(min=1, help="Maximum concurrent uploads")] = 4,
):
    context = _context(ctx)
    configs = context.configs
    dryrun = context.dryrun
    mscz_dir = context.path
    verbose = context.verbose

    mscz_dir = _require_mscz_path(mscz_dir)
    mscz_paths = _get_valid_mscz_paths(mscz_dir)

    if not mscz_paths:
        typer.echo("No MSCZ files found")
        if dryrun:
            typer.echo("Would upload 0 scores.")
        return

    if bucket is None:
        bucket = configs.mscz_bucket_name()
    if bucket is None:
        typer.echo("S3 bucket not set")
        raise typer.Exit(code=1)

    if verbose:
        typer.echo(f"Using bucket: {bucket}")
        typer.echo(f"Using S3 endpoint: {configs.aws_endpoint_url_s3()}")

    planned: list[tuple[Path, Score, str]] = []
    destinations: dict[str, list[Path]] = {}
    for path in mscz_paths:
        score = Score(path, read_metadata=True)
        key = score.normalized_name(with_key=True)
        planned.append((path, score, key))
        destinations.setdefault(unicodedata.normalize("NFC", key), []).append(path)
    collisions = {key: paths for key, paths in destinations.items() if len(paths) > 1}
    if collisions:
        for key, paths in collisions.items():
            typer.echo(f"Duplicate S3 key {key} for: {', '.join(str(path) for path in paths)}")
        raise typer.Exit(code=1)

    store = S3Store.connect(
        bucket=bucket,
        access_key_id=configs.aws_access_key_id(),
        secret_access_key=configs.aws_secret_access_key(),
        endpoint_url=configs.aws_endpoint_url_s3(),
        max_pool_connections=jobs,
    )
    summary = {status: 0 for status in SyncStatus}
    failures = 0

    with Progress() as progress:
        task = progress.add_task("Uploading scores...", total=len(mscz_paths))
        results: list[tuple[str, SyncStatus | None, Exception | None]] = [(key, None, None) for _, _, key in planned]

        def run(item: tuple[Path, Score, str]) -> SyncStatus:
            path, score, key = item
            return sync_file(store, score.absolute_path, key, dryrun=dryrun).status

        with ThreadPoolExecutor(max_workers=jobs) as executor:
            futures = {executor.submit(run, item): index for index, item in enumerate(planned)}
            for future in as_completed(futures):
                index = futures[future]
                try:
                    results[index] = (planned[index][2], future.result(), None)
                except Exception as error:
                    results[index] = (planned[index][2], None, error)
                progress.advance(task)

        for index, (s3_key, status, error) in enumerate(results):
            if error is not None:
                progress.console.print(f"Failed to upload {planned[index][0].name}: {error}")
                failures += 1
                continue
            assert status is not None
            summary[status] += 1
            if verbose:
                match status:
                    case SyncStatus.CREATED | SyncStatus.UPDATED:
                        action = "would upload" if dryrun else "uploading"
                        progress.console.print(
                            f"{s3_key} {'does not exist on S3' if status is SyncStatus.CREATED else 'differs from S3'}; {action} new version"
                        )
                    case SyncStatus.UNCHANGED:
                        progress.console.print(f"{s3_key} on S3 is up to date; skipping upload")
                    case SyncStatus.REMOTE_NEWER:
                        progress.console.print(f"{s3_key} on S3 is newer; skipping upload")
                    case SyncStatus.CONFLICT:
                        progress.console.print(f"{s3_key} conflicts with S3 at the same timestamp; skipping upload")

    if dryrun:
        progress.console.print(
            "Would upload "
            f"{summary[SyncStatus.CREATED] + summary[SyncStatus.UPDATED]} scores, "
            f"skip {summary[SyncStatus.UNCHANGED] + summary[SyncStatus.REMOTE_NEWER]}, "
            f"and leave {summary[SyncStatus.CONFLICT]} conflicts."
        )

    if failures:
        raise typer.Exit(code=1)


def _get_valid_mscz_paths(path: Path) -> list[Path]:
    if path.is_dir():
        mscz_paths = sorted(
            candidate for candidate in path.iterdir() if candidate.is_file() and candidate.suffix.lower() == ".mscz"
        )
    elif path.is_file() and path.suffix.lower() == ".mscz":
        mscz_paths = [path]
    else:
        typer.echo(f"{path} is not a valid mscz file or directory")
        raise typer.Exit(code=1)

    return mscz_paths
