import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import questionary
import typer
from rich.progress import Progress

from msm.config import Configs, DriveTargetConfig, S3TargetConfig, TargetConfig
from msm.export import png_export_status, to_pngs
from msm.gdrive import DriveSession
from msm.musescore import Musescore
from msm.remote import Artifact, RemoteTarget, SyncResult, SyncStatus
from msm.s3 import S3Store, S3Target
from msm.score import Score

app = typer.Typer(no_args_is_help=True)
normalize_app = typer.Typer(no_args_is_help=True, help="Normalize local files.")
export_app = typer.Typer(no_args_is_help=True, help="Export local files to another format.")
sync_app = typer.Typer(no_args_is_help=True, help="Synchronize local files to a remote target.")
app.add_typer(normalize_app, name="normalize")
app.add_typer(export_app, name="export")
app.add_typer(sync_app, name="sync")

SCORE_MEDIA_TYPE = "application/x-musescore"
PNG_MEDIA_TYPE = "image/png"


@dataclass
class AppContext:
    configs: Configs
    dryrun: bool
    verbose: bool


def _context(ctx: typer.Context) -> AppContext:
    if not isinstance(ctx.obj, AppContext):
        raise RuntimeError("Application context was not initialized")
    return ctx.obj


@app.callback()
def global_args(
    ctx: typer.Context,
    profile: Annotated[str, typer.Option(help="Configuration profile to use")] = "default",
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    dryrun: Annotated[bool, typer.Option("--dryrun", help="Preview changes without mutating files or remotes")] = False,
):
    """Manage MuseScore source files and exported artifacts."""
    ctx.obj = AppContext(configs=Configs(profile_name=profile), dryrun=dryrun, verbose=verbose)


def _require_path(path: Path | None, configured: Path | None, kind: str) -> Path:
    selected = path or configured
    if selected is None:
        typer.echo(f"{kind} path is not configured")
        raise typer.Exit(code=1)
    if not selected.exists():
        typer.echo(f"{kind} path {selected} does not exist")
        raise typer.Exit(code=1)
    return selected


def _paths(path: Path, suffix: str) -> list[Path]:
    if path.is_dir():
        return sorted(
            candidate for candidate in path.iterdir() if candidate.is_file() and candidate.suffix.lower() == suffix
        )
    if path.is_file() and path.suffix.lower() == suffix:
        return [path]
    typer.echo(f"{path} is not a valid {suffix} file or directory")
    raise typer.Exit(code=1)


def _score_paths(context: AppContext, path: Path | None) -> list[Path]:
    return _paths(_require_path(path, context.configs.local_mscz_directory(), "Score"), ".mscz")


def _png_paths(context: AppContext, path: Path | None) -> list[Path]:
    return _paths(_require_path(path, context.configs.local_png_directory(), "PNG"), ".png")


@normalize_app.command("scores")
def normalize_scores(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Option(help="Score directory or one .mscz file")] = None,
):
    """Normalize local score filenames from embedded metadata."""
    context = _context(ctx)
    scores = _score_paths(context, path)

    with Progress() as progress:
        task = progress.add_task("Normalizing scores...", total=len(scores))
        for score_path in scores:
            if context.verbose:
                progress.console.print(f"Normalizing {score_path}")
            if not context.dryrun:
                Score(path=score_path, read_metadata=True).normalize()
            progress.advance(task)
        if context.dryrun:
            progress.console.print(f"Would normalize {len(scores)} scores.")


@export_app.command("scores")
def export_scores(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Option(help="Score directory or one .mscz file")] = None,
    output: Annotated[Path | None, typer.Option(help="Directory to export PNGs to")] = None,
    jobs: Annotated[int | None, typer.Option(min=1, help="Maximum concurrent exports")] = None,
):
    """Export local scores to PNG files."""
    context = _context(ctx)
    configs = context.configs
    jobs = jobs if jobs is not None else configs.jobs()
    output = output or configs.local_png_directory()
    if output is None and not context.dryrun:
        typer.echo("PNG output directory is not configured")
        raise typer.Exit(code=1)

    score_paths = _score_paths(context, path)
    musescore = Musescore(configs.mscore_cmd())
    scores_to_export = 0
    pngs_to_export = 0

    with Progress() as progress:
        task = progress.add_task(f"Exporting scores ({jobs} jobs)...", total=len(score_paths))
        if context.dryrun:
            for score_path in score_paths:
                if context.verbose:
                    progress.console.print(f"Exporting {score_path}")
                score = Score(path=score_path)
                if output is None:
                    pages = musescore.metadata(score).pages
                else:
                    target_path = output / score.normalized_name(with_key=True, suffix="png")
                    _, _, up_to_date = png_export_status(score, target_path)
                    pages = None if up_to_date else musescore.metadata(score).pages
                if pages is not None:
                    if pages < 1:
                        raise RuntimeError("MuseScore metadata did not include a positive page count")
                    scores_to_export += 1
                    pngs_to_export += pages
                progress.advance(task)
        else:
            assert output is not None
            planned = [(score_path, Score(score_path)) for score_path in score_paths]
            _reject_collisions((score.normalized_name(with_key=True, suffix="png"), path) for path, score in planned)
            results: list[list[Path] | Exception | None] = [None] * len(planned)

            def run_export(item: tuple[Path, Score]) -> list[Path]:
                return to_pngs(item[1], musescore=Musescore(configs.mscore_cmd()), key=None, base_dir=output)

            with ThreadPoolExecutor(max_workers=jobs) as executor:
                futures = {executor.submit(run_export, item): index for index, item in enumerate(planned)}
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as error:
                        results[index] = error
                    progress.advance(task)

            failures = 0
            for index, result in enumerate(results):
                if isinstance(result, Exception):
                    progress.console.print(f"Failed to export {planned[index][0].name}: {result}")
                    failures += 1
                elif context.verbose and result:
                    for exported in result:
                        progress.console.print(f"\t{exported}")
            if failures:
                raise typer.Exit(code=1)

        if context.dryrun:
            progress.console.print(f"Would export {scores_to_export} scores and {pngs_to_export} PNGs.")


def _reject_collisions(named_paths) -> None:
    destinations: dict[str, list[Path]] = {}
    display_names: dict[str, str] = {}
    for name, path in named_paths:
        normalized = unicodedata.normalize("NFC", name)
        destinations.setdefault(normalized, []).append(path)
        display_names[normalized] = name
    collisions = {name: paths for name, paths in destinations.items() if len(paths) > 1}
    if collisions:
        for name, paths in collisions.items():
            typer.echo(f"Duplicate remote name {display_names[name]} for: {', '.join(str(path) for path in paths)}")
        raise typer.Exit(code=1)


def _score_artifacts(paths: list[Path]) -> list[Artifact]:
    artifacts = [
        Artifact(
            path=path, name=Score(path, read_metadata=True).normalized_name(with_key=True), media_type=SCORE_MEDIA_TYPE
        )
        for path in paths
    ]
    _reject_collisions((artifact.name, artifact.path) for artifact in artifacts)
    return artifacts


def _png_artifacts(paths: list[Path]) -> list[Artifact]:
    artifacts = [Artifact(path=path, name=path.name, media_type=PNG_MEDIA_TYPE) for path in paths]
    _reject_collisions((artifact.name, artifact.path) for artifact in artifacts)
    return artifacts


def _create_target(config: TargetConfig, jobs: int) -> RemoteTarget:
    if isinstance(config, S3TargetConfig):
        store = S3Store.connect(
            bucket=config.bucket,
            access_key_id=config.access_key_id,
            secret_access_key=config.secret_access_key,
            endpoint_url=config.endpoint_url,
            max_pool_connections=jobs,
        )
        return S3Target(store, config.prefix)
    if isinstance(config, DriveTargetConfig):
        session = DriveSession.from_credentials(
            config.credentials_path,
            config.folder_id,
            config.token_path,
        )
        session.validate_folder()
        return session
    raise TypeError(f"Unsupported target configuration: {config}")


def _resolve_target(context: AppContext, kind: str, target_name: str | None, jobs: int) -> tuple[str, RemoteTarget]:
    selected = target_name or context.configs.default_target(kind)
    if not selected:
        typer.echo(f"No remote target selected; use --target or configure DEFAULT_{kind.upper()}_TARGET")
        raise typer.Exit(code=1)
    try:
        config = context.configs.target(selected)
        return selected, _create_target(config, jobs)
    except Exception as error:
        typer.echo(f"Failed to connect to target '{selected}': {error}")
        raise typer.Exit(code=1)


def _sync_artifacts(
    context: AppContext, kind: str, artifacts: list[Artifact], target_name: str | None, jobs: int
) -> None:
    if not artifacts:
        label = "PNG" if kind == "pngs" else "score"
        typer.echo(f"No {label} files found")
        if context.dryrun:
            typer.echo(f"Would sync 0 {kind}.")
        return

    selected, target = _resolve_target(context, kind, target_name, jobs)
    results: list[SyncResult | Exception | None] = [None] * len(artifacts)
    worker_count = jobs if target.concurrent else 1

    with Progress() as progress:
        task = progress.add_task(f"Syncing {kind} to {selected} ({worker_count} jobs)...", total=len(artifacts))

        def run(index: int) -> None:
            try:
                results[index] = target.sync(artifacts[index], dryrun=context.dryrun)
            except Exception as error:
                results[index] = error

        if worker_count == 1:
            for index in range(len(artifacts)):
                run(index)
                progress.advance(task)
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = {
                    executor.submit(target.sync, artifact, context.dryrun): index
                    for index, artifact in enumerate(artifacts)
                }
                for future in as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as error:
                        results[index] = error
                    progress.advance(task)

        conflicts: list[int] = []
        failures = 0
        for index, (artifact, result) in enumerate(zip(artifacts, results, strict=True)):
            if isinstance(result, Exception):
                progress.console.print(f"Failed to sync {artifact.name}: {result}")
                failures += 1
                continue
            assert result is not None
            detail = f": {result.error}" if result.error else ""
            action = result.status.value.replace("-", " ").capitalize()
            if context.verbose or result.status in {SyncStatus.CONFLICT, SyncStatus.REMOTE_NEWER}:
                prefix = (
                    "Would be " if context.dryrun and result.status in {SyncStatus.CREATED, SyncStatus.UPDATED} else ""
                )
                progress.console.print(f"{prefix}{action} {result.name}{detail}")
            if result.status in {SyncStatus.CONFLICT, SyncStatus.REMOTE_NEWER}:
                failures += 1
            if result.status is SyncStatus.CONFLICT:
                conflicts.append(index)

    if conflicts:
        choice = questionary.select(
            f"{len(conflicts)} conflict(s). What should be done?",
            choices=["Force-push all", "Ignore", "Review one by one"],
        ).ask()
        if choice is None:
            raise typer.Abort()
        if choice == "Force-push all":
            force_indices = conflicts
        elif choice == "Review one by one":
            remaining_conflicts = []
            force_indices = []
            for index in conflicts:
                if typer.confirm(f"Force-push {artifacts[index].name}?", default=False):
                    force_indices.append(index)
                else:
                    remaining_conflicts.append(index)
            conflicts = remaining_conflicts
        else:
            force_indices = []
            conflicts = []

        if force_indices:
            with Progress() as progress:
                task = progress.add_task("Force-pushing conflicts...", total=len(force_indices))
                for index in force_indices:
                    try:
                        results[index] = target.sync(artifacts[index], dryrun=context.dryrun, force=True)
                    except Exception as error:
                        results[index] = error
                    progress.advance(task)

    summary = {status: 0 for status in SyncStatus}
    for result in results:
        if isinstance(result, SyncResult):
            summary[result.status] += 1
    failures = 0
    for result in results:
        if isinstance(result, Exception):
            failures += 1
        elif isinstance(result, SyncResult) and result.status is SyncStatus.REMOTE_NEWER:
            failures += 1
    if conflicts:
        for index in conflicts:
            result = results[index]
            if isinstance(result, SyncResult) and result.status is SyncStatus.CONFLICT:
                failures += 1

    prefix = "Would sync" if context.dryrun else "Synced"
    typer.echo(
        f"{prefix}: {summary[SyncStatus.CREATED]} created, {summary[SyncStatus.UPDATED]} updated, "
        f"{summary[SyncStatus.UNCHANGED]} unchanged, {summary[SyncStatus.REMOTE_NEWER]} remote newer, "
        f"{summary[SyncStatus.CONFLICT]} conflicts."
    )
    if failures:
        raise typer.Exit(code=1)


@sync_app.command("scores")
def sync_scores(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Option(help="Score directory or one .mscz file")] = None,
    target: Annotated[str | None, typer.Option(help="Named remote target")] = None,
    jobs: Annotated[int | None, typer.Option(min=1, help="Maximum concurrent synchronizations")] = None,
):
    """Synchronize score files to a named remote target."""
    context = _context(ctx)
    selected_jobs = jobs if jobs is not None else context.configs.jobs()
    _sync_artifacts(context, "scores", _score_artifacts(_score_paths(context, path)), target, selected_jobs)


@sync_app.command("pngs")
def sync_pngs(
    ctx: typer.Context,
    path: Annotated[Path | None, typer.Option(help="PNG directory or one .png file")] = None,
    target: Annotated[str | None, typer.Option(help="Named remote target")] = None,
    jobs: Annotated[int | None, typer.Option(min=1, help="Maximum concurrent synchronizations")] = None,
):
    """Synchronize exported PNG files to a named remote target."""
    context = _context(ctx)
    selected_jobs = jobs if jobs is not None else context.configs.jobs()
    _sync_artifacts(context, "pngs", _png_artifacts(_png_paths(context, path)), target, selected_jobs)
