# Musescore file management

## Getting started

### First time

1. Install the project and development dependencies:

    ```bash
    uv sync --extra s3 --group dev
    make init
    ```

1. Create configuration/credentials file at `~/.msm/configs`, with values:

    ```ini
    [default]
    LOCAL_MSCZ_DIRECTORY=<Local path to where .mscz files are stored>
    LOCAL_PNG_DIRECTORY=<Local path to where PNG files are exported>
    MSCORE_CMD=<MuseScore command; defaults to mscore>

    AWS_ACCESS_KEY_ID=<AWS access key ID for S3 storage>
    AWS_SECRET_ACCESS_KEY=<AWS secret access key for S3 storage>
    AWS_ENDPOINT_URL_S3=https://...
    MSCZ_BUCKET_NAME=<S3 bucket name>
    ```

Environment variables override values from the selected profile. Use `--profile` to select a profile other than
`default`.

## Usage

```bash
uv run msm --help
uv run msm --path ./scores normalize
uv run msm --path ./scores export-pngs --export-dir ./pngs
uv run msm --path ./scores upload
```

Pass `--dryrun` before the command to preview filesystem or S3 changes. S3 previews read remote object metadata but do
not upload files.
