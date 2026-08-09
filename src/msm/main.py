import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import questionary
import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from msm.config import PROFILE_FIELDS, TARGET_NAME, Configs, DriveTargetConfig, S3TargetConfig, TargetConfig
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
targets_app = typer.Typer(invoke_without_command=True, help="Manage configured remote targets.")
profiles_app = typer.Typer(invoke_without_command=True, help="Manage configuration profiles.")
app.add_typer(normalize_app, name="normalize")
app.add_typer(export_app, name="export")
app.add_typer(sync_app, name="sync")
app.add_typer(targets_app, name="targets")
app.add_typer(profiles_app, name="profiles")

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
        Artifact(path=path, name=Score(path, read_metadata=True).normalized_name(), media_type=SCORE_MEDIA_TYPE)
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


@targets_app.command("list")
def list_targets(ctx: typer.Context):
    """List configured remote targets with non-sensitive settings."""
    context = _context(ctx)
    targets = context.configs.targets()
    if not targets:
        typer.echo("No remote targets configured.")
        return
    table = Table()
    table.add_column("TARGET")
    table.add_column("TYPE")
    table.add_column("SETTINGS")
    for name, provider in targets.items():
        settings = _target_display_settings(context, name, provider)
        table.add_row(name, provider or "unknown", settings)
    Console().print(table)


def _target_display_settings(context: AppContext, name: str, provider: str, *, separator: str = "\n") -> str:
    try:
        details = context.configs.target_display_values(name)
    except Exception:
        details = ()
    if provider == "s3" and len(details) == 2:
        settings = {"bucket": details[0], "endpoint": details[1]}
        return separator.join(f"{key}={settings[key]}" for key in sorted(settings))
    return details[0] if details else "-"


def _ask_target_text(message: str, description: str, *, password: bool = False) -> str:
    prompt = questionary.password if password else questionary.text
    answer = prompt(message, instruction=description).ask()
    if answer is None:
        raise typer.Abort()
    return answer.strip()


def _ask_optional_target_value(setting: str, description: str, *, password: bool = False) -> str | None:
    value = _ask_target_text(f"{setting}:", description, password=password)
    return value or None


def _target_values_prompt(provider: str) -> dict[str, str]:
    if provider == "s3":
        values = {
            "TYPE": "s3",
            "BUCKET": _ask_target_text("S3 bucket:", "The bucket where synchronized files will be stored."),
        }
        optional = {
            "PREFIX": "Optional path prefix inside the bucket, useful for keeping files in a folder.",
            "ENDPOINT_URL": "Optional S3-compatible service endpoint; leave unset for AWS S3.",
            "ACCESS_KEY_ID": "Optional access key; leave unset to use the standard AWS credential chain.",
            "SECRET_ACCESS_KEY": "Optional secret key paired with ACCESS_KEY_ID.",
        }
        for setting, description in optional.items():
            value = _ask_optional_target_value(setting, description, password="SECRET" in setting)
            if value is not None:
                values[setting] = value
        return values

    values = {
        "TYPE": "google-drive",
        "FOLDER_ID": _ask_target_text(
            "Google Drive folder ID:", "The ID of the Drive folder that will contain synchronized files."
        ),
        "CREDENTIALS_PATH": _ask_target_text(
            "Google OAuth credentials path:",
            "Path to the Google client credentials JSON downloaded from Google Cloud.",
        ),
    }
    token = _ask_optional_target_value(
        "TOKEN_PATH",
        "Optional path for the cached OAuth token; otherwise a target-specific default is used.",
    )
    if token is not None:
        values["TOKEN_PATH"] = token
    return values


def _target_state(name: str, values: dict[str, str]) -> str:
    lines = [f"Target: {name}"]
    for key, value in sorted(values.items()):
        display = "********" if key in {"ACCESS_KEY_ID", "SECRET_ACCESS_KEY"} else value
        lines.append(f"  {key}={display}")
    return "\n".join(lines)


@targets_app.command("add")
def add_target(
    ctx: typer.Context,
    target_name: Annotated[str | None, typer.Argument(help="Name for the new remote target")] = None,
):
    """Interactively create a remote target."""
    context = _context(ctx)
    name = target_name or _ask_target_text(
        "New target name:", "Letters, numbers, dots, hyphens, and underscores are allowed."
    )
    if not name or not TARGET_NAME.fullmatch(name):
        raise typer.BadParameter("Target name must contain only letters, numbers, dots, hyphens, and underscores")
    if name in context.configs.targets():
        typer.echo(f"Remote target '{name}' already exists")
        raise typer.Exit(code=1)
    provider = questionary.select(
        "Which storage provider should this target use?",
        choices=["s3", "google-drive"],
    ).ask()
    if provider is None:
        raise typer.Abort()
    values = _target_values_prompt(provider)
    typer.echo(_target_state(name, values))
    if not typer.confirm("Write this target to the configuration file?", default=False):
        typer.echo("No changes made.")
        return
    if context.dryrun:
        typer.echo("Dry run: no changes made.")
        return
    context.configs.save_target(name, provider, values)
    typer.echo(f"Target '{name}' added.")


@profiles_app.command("list")
def list_profiles(ctx: typer.Context):
    """List configured profiles and their settings."""
    context = _context(ctx)
    profiles = context.configs.profiles()
    if not profiles:
        typer.echo("No profiles configured.")
        return
    table = Table()
    table.add_column("PROFILE")
    table.add_column("SETTINGS")
    for name in profiles:
        values = context.configs.profile_values(name)
        settings = "\n".join(f"{key}={value}" for key, value in sorted(values.items())) or "-"
        table.add_row(name, settings)
    Console().print(table)


@profiles_app.callback()
def profiles(ctx: typer.Context):
    """Show profile help and list profiles when no command is supplied."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        list_profiles(ctx)


def _ask_profile_text(message: str, description: str, default: str | None = None) -> str:
    answer = questionary.text(message, default=default or "", instruction=description).ask()
    if answer is None:
        raise typer.Abort()
    return answer.strip()


def _ask_optional_profile_value(setting: str, description: str, current: str | None = None) -> str | None:
    if not questionary.confirm(f"Configure {setting}? {description}", default=current is not None).ask():
        return None
    value = _ask_profile_text(
        f"{setting}:",
        description,
        current,
    )
    return value or None


def _profile_values_prompt(existing: dict[str, str]) -> dict[str, str]:
    values = {
        "LOCAL_MSCZ_DIRECTORY": _ask_profile_text(
            "Directory containing MuseScore .mscz files:",
            "Used by normalize, export, and score sync commands.",
            existing.get("LOCAL_MSCZ_DIRECTORY"),
        ),
        "LOCAL_PNG_DIRECTORY": _ask_profile_text(
            "Directory containing exported PNG files:",
            "Used by PNG sync commands and as the default export destination.",
            existing.get("LOCAL_PNG_DIRECTORY"),
        ),
    }
    if not values["LOCAL_MSCZ_DIRECTORY"] or not values["LOCAL_PNG_DIRECTORY"]:
        raise typer.BadParameter("The local score and PNG directories are required")

    optional = {
        "MSCORE_CMD": "MuseScore executable used to export scores (default: mscore).",
        "JOBS": "Maximum number of parallel export or sync jobs (default: 4).",
        "DEFAULT_SCORES_TARGET": "Named remote target used by score sync when --target is omitted.",
        "DEFAULT_PNGS_TARGET": "Named remote target used by PNG sync when --target is omitted.",
    }
    for setting, description in optional.items():
        value = _ask_optional_profile_value(setting, description, existing.get(setting))
        if value is not None:
            values[setting] = value
    return values


def _profile_state(name: str, values: dict[str, str]) -> str:
    lines = [f"Profile: {name}"]
    defaults = {"MSCORE_CMD": "mscore", "JOBS": "4"}
    for setting in PROFILE_FIELDS:
        value = values.get(setting)
        if value is None:
            value = f"<not set; default: {defaults[setting]}>" if setting in defaults else "<not set>"
        lines.append(f"  {setting}={value}")
    return "\n".join(lines)


def _profile_name(name: str | None, prompt: str) -> str:
    selected = name or _ask_profile_text(prompt, "Letters, numbers, dots, hyphens, and underscores are allowed.")
    if not selected:
        raise typer.BadParameter("Profile name cannot be empty")
    return selected


@profiles_app.command("add")
def add_profile(
    ctx: typer.Context,
    profile_name: Annotated[str | None, typer.Argument(help="Name for the new profile")] = None,
):
    """Interactively create a configuration profile."""
    context = _context(ctx)
    name = _profile_name(profile_name, "New profile name:")
    if name in context.configs.profiles():
        typer.echo(f"Profile '{name}' already exists")
        raise typer.Exit(code=1)
    values = _profile_values_prompt({})
    typer.echo(_profile_state(name, values))
    if not typer.confirm("Write this profile to the configuration file?", default=False):
        typer.echo("No changes made.")
        return
    if context.dryrun:
        typer.echo("Dry run: no changes made.")
        return
    context.configs.save_profile(name, values)
    typer.echo(f"Profile '{name}' added.")


@profiles_app.command("edit")
def edit_profile(
    ctx: typer.Context,
    profile_name: Annotated[str | None, typer.Argument(help="Profile to edit")] = None,
):
    """Interactively edit a configuration profile."""
    context = _context(ctx)
    profiles = context.configs.profiles()
    name = profile_name
    if name is None:
        if not profiles:
            typer.echo("No profiles configured.")
            raise typer.Exit(code=1)
        choices = {profile: profile for profile in profiles}
        choice = questionary.select("Which profile should be edited?", choices=list(choices)).ask()
        if choice is None:
            raise typer.Abort()
        name = choices[choice]
    if name not in profiles:
        typer.echo(f"Profile '{name}' not found in configuration file")
        raise typer.Exit(code=1)
    values = _profile_values_prompt(context.configs.profile_values(name))
    typer.echo(_profile_state(name, values))
    if not typer.confirm("Write this profile to the configuration file?", default=False):
        typer.echo("No changes made.")
        return
    if context.dryrun:
        typer.echo("Dry run: no changes made.")
        return
    context.configs.save_profile(name, values)
    typer.echo(f"Profile '{name}' updated.")


@profiles_app.command("clear")
def clear_profile(
    ctx: typer.Context,
    profile_name: Annotated[str | None, typer.Argument(help="Profile to remove")] = None,
):
    """Remove a configuration profile."""
    context = _context(ctx)
    profiles = context.configs.profiles()
    if not profiles:
        typer.echo("No profiles configured.")
        raise typer.Exit(code=1)
    name = profile_name
    if name is None:
        name = questionary.select("Which profile should be cleared?", choices=profiles).ask()
        if name is None:
            raise typer.Abort()
    if name not in profiles:
        typer.echo(f"Profile '{name}' not found in configuration file")
        raise typer.Exit(code=1)
    if not typer.confirm(f"Clear profile '{name}'?", default=False):
        typer.echo("No changes made.")
        return
    if context.dryrun:
        typer.echo("Dry run: no changes made.")
        return
    context.configs.clear_profile(name)
    typer.echo(f"Profile '{name}' cleared.")


@targets_app.callback()
def targets(ctx: typer.Context):
    """Show target help and list targets when no command is supplied."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        list_targets(ctx)


@targets_app.command("clear")
def clear_target(
    ctx: typer.Context,
    target_name: Annotated[str | None, typer.Argument(help="Configured remote target name")] = None,
):
    """Clear all data from a configured remote target."""
    context = _context(ctx)
    targets = context.configs.targets()
    if not targets:
        typer.echo("No remote targets configured.")
        raise typer.Exit(code=1)
    selected = target_name
    if selected is None:
        choices = {
            f"{name} ({_target_display_settings(context, name, provider, separator=', ')})": name
            for name, provider in targets.items()
        }
        choice = questionary.select("Which target should be cleared?", choices=list(choices)).ask()
        if choice is None:
            raise typer.Abort()
        selected = choices[choice]
    if selected not in targets:
        typer.echo(f"Remote target '{selected}' not found in configuration file")
        raise typer.Exit(code=1)
    try:
        target = _create_target(context.configs.target(selected), context.configs.jobs())
        with Progress() as progress:
            task = progress.add_task(f"Clearing {selected}...", total=None)
            completed = 0

            def advance(amount: int, total: int | None) -> None:
                nonlocal completed
                completed += amount
                progress.update(task, advance=amount, total=total)
                if amount == 0 and total is not None:
                    progress.console.print(f"Found {total} items in {selected}.")
                elif amount:
                    action = "Would delete" if context.dryrun else "Deleted"
                    suffix = f"/{total}" if total is not None else ""
                    progress.console.print(f"{action} {completed}{suffix} items from {selected}.")

            target.clear(dryrun=context.dryrun, progress=advance)
    except Exception as error:
        typer.echo(f"Failed to clear target '{selected}': {error}")
        raise typer.Exit(code=1)
    typer.echo(f"{'Would clear' if context.dryrun else 'Cleared'} target '{selected}'.")


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
