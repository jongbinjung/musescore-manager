# Musescore file management

## Getting started

### First time

1. Install the project and development dependencies:

    ```bash
    uv sync --extra gcs --extra s3 --group dev
    make init
    ```

1. Create configuration/credentials file at `~/.msm/configs`, with values:

    ```ini
    [default]
    LOCAL_MSCZ_DIRECTORY=<Local path to where .mscz files are stored>
    LOCAL_PNG_DIRECTORY=<Local path to where PNG files are exported>
    MSCORE_CMD=<MuseScore command; defaults to mscore>

    GOOGLE_DRIVE_FOLDER_ID=<Google Drive destination folder ID>
    GOOGLE_APP_CREDENTIALS_JSON_PATH=<Path to Google OAuth client credentials JSON>

    AWS_ACCESS_KEY_ID=<AWS access key ID for S3 storage>
    AWS_SECRET_ACCESS_KEY=<AWS secret access key for S3 storage>
    AWS_ENDPOINT_URL_S3=https://...
    MSCZ_BUCKET_NAME=<S3 bucket name>
    ```
Environment variables override values from the selected profile. Use `--profile` to select a profile other than
`default`.

Google Drive support requires the optional dependencies installed with `uv sync --extra gcs`. The command requests
access to Google Drive so it can use a destination folder configured by ID. The first `sync-pngs` run opens a browser
for OAuth authorization and stores the refresh token at `~/.msm/google-drive-token.json`.

## Usage

```bash
uv run msm --help
uv run msm --path ./scores normalize
uv run msm --path ./scores export-pngs --export-dir ./pngs
uv run msm --path ./scores upload
uv run msm sync-pngs
```

Preview synchronization with:

```bash
uv run msm --dryrun sync-pngs
```

The command compares PNG checksums with app-managed files in the configured folder. Unchanged files are skipped,
changed files are updated, and missing files are created. A same-name file not managed by this application, or
multiple managed files with the same name, is reported as a conflict and left unchanged. Drive synchronization is
one-way and does not remove remote files that are absent locally.

Pass `--dryrun` before a command to preview filesystem, S3, or Drive mutations. Remote data is not changed, but a
Drive preview can still open the OAuth flow and write or refresh the local token.
