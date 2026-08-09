# Musescore file management

A CLI interface for managing Musescore files and their PNG exports. It can normalize Musescore files, export PNGs, and synchronize them to
Google Drive or S3-compatible services.

## Installation

Install the project and the providers you use:

```bash
uv sync --extra drive --extra s3 --group dev
make init
```

Google Drive uses the `drive` extra. S3 and S3-compatible services use the `s3` extra.

## Configuration

Create `~/.msm/configs`:

```ini
[default]
LOCAL_MSCZ_DIRECTORY=/path/to/scores
LOCAL_PNG_DIRECTORY=/path/to/pngs
MSCORE_CMD=mscore
JOBS=4
DEFAULT_SCORES_TARGET=archive
DEFAULT_PNGS_TARGET=gallery

[target.archive]
TYPE=s3
BUCKET=my-score-bucket
PREFIX=scores
ENDPOINT_URL=https://s3.example.com

[target.gallery]
TYPE=google-drive
FOLDER_ID=<Google Drive folder ID>
CREDENTIALS_PATH=~/.config/msm/google-client.json
```

The `[default]` section is a profile. Select another profile with `--profile NAME`. Normal application environment
variables override profile values.

Profiles can be managed interactively. With no subcommand, `profiles` prints its help and the current profile list:

```bash
uv run msm profiles
uv run msm profiles add work
uv run msm profiles edit work
uv run msm profiles clear work
```

The add and edit wizards explain each setting, allow optional settings to be skipped, and show the complete proposed
profile for confirmation before changing `~/.msm/configs`.

Each `[target.NAME]` section defines a reusable destination. Select one with `--target NAME`, regardless of whether it
uses Google Drive or S3. Target fields can be overridden with `MSM_TARGET_<NAME>_<FIELD>`, with punctuation in the name
replaced by underscores. For example, `MSM_TARGET_ARCHIVE_BUCKET` overrides `BUCKET` for `archive`.

Targets can also be created interactively:

```bash
uv run msm targets add archive
```

The wizard asks for the provider's required settings, offers optional settings individually, and confirms the complete
target before changing `~/.msm/configs`.

S3 targets support `BUCKET`, `PREFIX`, `ENDPOINT_URL`, `ACCESS_KEY_ID`, and `SECRET_ACCESS_KEY`. Standard
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_ENDPOINT_URL_S3` values are used as fallbacks, so the normal boto3
credential chain remains available.

Google Drive targets support `FOLDER_ID`, `CREDENTIALS_PATH`, and an optional `TOKEN_PATH`. By default, OAuth tokens are
stored per target at `~/.msm/tokens/google-drive-<target>.json`. The first use can open a browser for authorization.

## Commands

Local operations use `normalize` and `export`:

```bash
uv run msm normalize scores
uv run msm normalize scores --path ./scores
uv run msm export scores --path ./scores --output ./pngs --jobs 2
```

Remote operations use `sync`:

```bash
uv run msm sync scores
uv run msm sync scores --target gallery
uv run msm sync pngs --target archive
uv run msm --dryrun sync pngs --target gallery
```

Score commands default to `LOCAL_MSCZ_DIRECTORY`; PNG commands default to `LOCAL_PNG_DIRECTORY`. A path can be one file
or a directory. Directory scans are non-recursive.

`sync` is one-way and non-deleting. It creates missing remote files, updates older managed files, skips unchanged files,
and leaves remote-newer or conflicting files untouched. Conflicts and remote-newer results produce a nonzero exit code.
`--dryrun` prevents remote mutations but still reads remote state and can authorize Google Drive or refresh its token.

The former flat invocations `msm normalize`, `msm export-pngs`, `msm sync-pngs`, `msm upload`, and
`msm upload-scores` have been removed.
