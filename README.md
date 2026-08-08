# Musescore file management

## Getting started

### First time

1. run:

    ```bash
    make init
    ```

1. Create configuration/credentials file at `~/.msm/configs`, with values:

    ```ini
    [default]
    NOTION_TOKEN=<Notion integration token>

    LOCAL_MSCZ_DIRECTORY=<Local path to where .mscz files are stored>
    LOCAL_PNG_DIRECTORY=<Local path to exported PNG files>

    GOOGLE_DRIVE_FOLDER_ID=<Google Drive destination folder ID>
    GOOGLE_APP_CREDENTIALS_JSON_PATH=<Path to Google OAuth client credentials JSON>

    AWS_ACCESS_KEY_ID=<AWS access key ID for S3 storage>
    AWS_SECRET_ACCESS_KEY=<AWS secret access key for S3 storage>
    AWS_ENDPOINT_URL_S3=https://...
    AWS_ENDPOINT_URL_IAM=https://...
    AWS_REGION=...
    ```

Google Drive support requires the optional dependencies installed with `uv sync --extra gcs`.
The first `sync-pngs` run opens a browser for OAuth authorization. The refresh token is stored at
`~/.msm/google-drive-token.json`, not in the project directory.

Run a read-only synchronization preview with:

```bash
uv run msm --dryrun sync-pngs
```

The command compares PNG checksums with files in the configured folder. Unchanged files are skipped,
changed files are updated, and missing files are created. If multiple remote PNG files have the same
name, the latest modified file is updated and the older duplicates are deleted after a successful update.
