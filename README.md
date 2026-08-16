# gdls: Google Drive List

A command-line tool that lists Google Drive folder structures in an `ls`-like format.

It can display file and folder names, sizes, modification dates, owners, and more. It supports output in TSV / JSON formats, recursive exploration, shared drives, and aggregated information.

## Features

- Hierarchically explore Google Drive folders
- Support for both regular and shared drives
- Flexible output of file and folder attributes
- Recursive item enumeration and aggregation within folders
- Export in TSV / JSON formats
- Sort output results
- Readable table format and color display in terminal
- Standard output optimized for pipeline processing
- Read-only access via Google OAuth 2.0
- Automatic OAuth token refresh

## Requirements

- Python 3.10+
- Google Drive API
- Dependency packages listed in `requirements.txt`

## Installation

```bash
git clone https://github.com/snowflake36u/google-drive-lister
cd google-drive-lister
pip install -r requirements.txt
```

## Authentication

OAuth 2.0 credentials are required to use the Google Drive API.

1. Create a project in Google Cloud Console
2. Enable **Google Drive API**
3. Create **OAuth 2.0 Client ID (Desktop application)**
4. Download `client_secret.json`
5. Place it in one of the following locations:

| OS            | Location                                      |
| ------------- | --------------------------------------------- |
| Windows       | `%APPDATA%\SnowyTools\GDLS\client_secret.json` |
| macOS / Linux | `~/.config/SnowyTools/GDLS/client_secret.json` |

On the first run, you will be prompted to authenticate via your browser. A `token.json` file will be automatically generated in the same directory.

You can also change the authentication file location using the `--client-secret` / `--token-file` options or corresponding environment variables.

## Usage

### Basic Usage

```bash
python gdls.py <FOLDER_ID>
```

Lists items **directly under** the specified folder.

You can specify a folder ID, Google Drive folder URL, or file preview URL:

```bash
python gdls.py 1ABC123xyzABC123xyzABC123xyz
python gdls.py https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
python gdls.py root
python gdls.py /
```

By default, item names are output as TSV.

### Common Options

```bash
# Display detailed information (permissions, owners, size, modifiedTime, id, name)
python gdls.py <FOLDER_ID> -l

# Display information about the folder itself
python gdls.py <FOLDER_ID> -i

# Display in human-readable line format
python gdls.py <FOLDER_ID> -d

# Specify output fields
python gdls.py <FOLDER_ID> -f "id,name,size,createdTime,modifiedTime"

# Recursively enumerate files and folders
python gdls.py <FOLDER_ID> -R

# Output in JSON format
python gdls.py <FOLDER_ID> -j

# Output to file
python gdls.py <FOLDER_ID> -l -o output.tsv

# Append to existing file
python gdls.py <FOLDER_ID_1> -l -o combined.tsv
python gdls.py <FOLDER_ID_2> -l -o combined.tsv --append --no-header

# Sort (use ' desc' suffix for descending order)
python gdls.py <FOLDER_ID> -R -f "name,size" -S "size desc"

# Pipe processing (use `-q` / `--quiet` to suppress messages and logs)
python gdls.py <FOLDER_ID> -R -q | grep "\.pdf$"
```

## Output

### TSV

TSV is the default output format.

In the terminal, output is displayed as a table with auto-adjusted column widths. When redirected to files or piped, it is treated as tab-separated TSV.

```bash
python gdls.py <FOLDER_ID> -l
python gdls.py <FOLDER_ID> -l > files.tsv
```

Use `--no-header` to omit the TSV header.

### JSON (`-j` / `--json` option)

```bash
python gdls.py <FOLDER_ID> -Rj -o drive_data.json
```

This format is suitable for data processing and analysis.

### Terminal output

When outputting directly to the terminal, the following features are available:

- Color-coded display distinguishing files, folders, and shortcuts
- Column alignment that considers full-width characters like Japanese
- Progress bar during recursive exploration

Color display is automatically disabled when output is piped or redirected to files.

## Output fields

The `--fields` option supports standard Google Drive API fields plus the following custom fields:

| Field                 | Description                          |
| --------------------- | ------------------------------------ |
| `permissions`         | `ls`-style file type and permission display |
| `relativePath`        | Relative path from root             |
| `depth`               | Folder hierarchy depth              |
| `totalSize`           | Total size including descendant items |
| `totalQuotaBytesUsed` | Total quota used including descendants |
| `oldestCreatedTime`   | Oldest creation time including descendants |

When you specify aggregation fields like `totalSize`, `totalQuotaBytesUsed`, or `oldestCreatedTime`, descendant items are automatically explored as needed.

## Command-line options

| Option              | Description                          |
| ------------------- | ------------------------------------ |
| `target`            | Google Drive folder URL or ID       |
| `-R`, `--recursive` | Recursively retrieve descendant items |
| `--include-trashed` | Include items in trash              |
| `-i`, `--item`      | Retrieve the specified item itself   |
| `-d`, `--describe`  | Display detailed item information in human-readable format |
| `-l`, `--long`      | Display basic attributes in long format |
| `-f`, `--fields`    | Specify output fields               |
| `-S`, `--sort`      | Sort output                         |
| `-o`, `--output`    | Specify output file                 |
| `-a`, `--append`    | Append to existing output file       |
| `-j`, `--json`      | Output in JSON format               |
| `--no-header`       | Omit TSV header                     |
| `--log-level`       | Specify log level                   |
| `-q`, `--quiet`     | Suppress progress and normal logs   |
| `--client-secret`   | Specify path to `client_secret.json` |
| `--token-file`      | Specify path to `token.json`        |
| `-h`, `--help`      | Display help                        |

## Examples

### Get total folder size

```bash
python gdls.py <FOLDER_ID> -i -f "id,name,totalSize"
```

## Environment variables

You can also specify authentication file locations using environment variables.

### Windows PowerShell

```powershell
$env:GDLS_CLIENT_SECRET_FILE="C:\custom\path\client_secret.json"
$env:GDLS_TOKEN_FILE="C:\custom\path\token.json"

python gdls.py <FOLDER_ID>
```

### macOS / Linux

```bash
export GDLS_CLIENT_SECRET_FILE="/custom/path/client_secret.json"
export GDLS_TOKEN_FILE="/custom/path/token.json"

python gdls.py <FOLDER_ID>
```

Command-line options (`--client-secret`, `--token-file`) take precedence over environment variables.

## Troubleshooting

### `Client Secret file not found`

Verify that `client_secret.json` is in the correct location.

You can also specify the path directly via command-line argument:

```bash
python gdls.py <FOLDER_ID> --client-secret /path/to/client_secret.json
```

### Authentication errors like `invalid_grant`

Delete the existing `token.json` and reauthenticate.

**Windows**:

```powershell
Remove-Item "$env:APPDATA\SnowyTools\GDLS\token.json" -ErrorAction SilentlyContinue
```

**macOS / Linux**:

```bash
rm -f ~/.config/SnowyTools/GDLS/token.json
```

Then run `gdls.py` again.

### Folder not found

Check the following:

- Verify that the folder ID or URL is correct
- Verify that your authenticated Google account has access to the folder
- Verify that the target folder is not in trash
  - Use `--include-trashed` if you want to display items in trash

### Large folders take a long time to process

- Disable recursive exploration
  - Do not use `--recursive`
  - Do not specify aggregation fields in `--fields` (`totalSize`, `oldestCreatedTime`, etc.)
- Disable progress display with `--quiet`

## License

BSD 3-Clause

