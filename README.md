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

    AWS_ACCESS_KEY_ID=<AWS access key ID for S3 storage>
    AWS_SECRET_ACCESS_KEY=<AWS secret access key for S3 storage>
    AWS_ENDPOINT_URL_S3=https://...
    AWS_ENDPOINT_URL_IAM=https://...
    AWS_REGION=...
    ```
