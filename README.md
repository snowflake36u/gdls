# gdls: Google Drive List

`gdls` is a command-line tool for exploring Google Drive in an `ls`-like format.

It is designed for **inspecting the contents and structure of Google Drive**, rather than modifying them. It can recursively traverse folders, display metadata, calculate aggregate statistics, and export results in formats suitable for further processing.

## Features

- List files and folders in a Google Drive folder
- Traverse subfolders recursively with optional depth limits
- Display detailed metadata for individual items
- Calculate aggregate statistics such as total size and item counts
- Select fields to display or export
- Sort results by one or more fields
- Export results as TSV, CSV, or JSON
- Provide relative paths and hierarchy information for recursive results
- Render a compact table when output is connected to a terminal
- Work with read-only Google OAuth 2.0 credentials
- Automatically refresh expired authentication tokens

## Why `gdls`?

Google Drive can be accessed through many existing command-line tools. `gdls` focuses on a narrower use case: **inspecting Google Drive as a hierarchical items and output statistics of them in the table format**.

It provides an `ls`-like interface centered around read-only exploration, metadata, hierarchy, and aggregate statistics. File transfer, synchronization, and other operations that modify Drive are outside its scope.

## Requirements

- Python 3.10+
- A Google Cloud project with the Google Drive API enabled
- An OAuth 2.0 client ID for a desktop application
- Dependencies installed from `pyproject.toml`

## Installation

For local development / editable installation:

```bash
git clone https://github.com/snowflake36u/gdls
cd gdls
python -m pip install -e .
```

After installation:

```bash
gdls --help
```

> PyPI installation is not supported yet.

## Authentication

`gdls` uses OAuth 2.0 credentials to access the Google Drive API.

1. Create a project in Google Cloud Console.
2. Enable the Google Drive API.
3. Create an OAuth 2.0 client ID for a desktop application.
4. Download `client_secret.json`.
5. Place it in the default user-data directory, or specify it with `--client-secret`.

Default locations for the authentication token are:

| OS            | Location                                    |
| ------------- | ------------------------------------------- |
| Windows       | `%LOCALAPPDATA&\SnowyTools\gdls\token.json` |
| macOS / Linux | `~/.local/share/SnowyTools/gdls/token.json` |

On the first run, a browser window opens for authentication and the OAuth token is stored automatically.

You can override both paths with environment variables `GDLS_CLIENT_SECRET_FILE` and `GDLS_TOKEN_FILE`, or options:

```bash
gdls <TARGET> \
  --client-secret /path/to/client_secret.json \
  --token-file /path/to/token.json
```

## Usage

### Basic usage

```bash
gdls <TARGET>
```

This lists the items directly under the specified folder.

`<TARGET>` can be a folder/file ID, a Google Drive URL, `root`, or `/`.

```bash
gdls 1ABC123xyzABC123xyzABC123xyz
gdls https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
gdls https://drive.google.com/file/d/FILE_ID/view
gdls root
gdls /
```

### Examples

```bash
# Show the default long-format columns
# (permissions, owners, size, modifiedTime, id, name)
gdls <TARGET> -l

# Show selected fields
gdls <TARGET> -f "id,name,size,createdTime,modifiedTime"

# Show detailed metadata for a single item
gdls <TARGET> -D

# Recursively list items under a folder
gdls <TARGET> -R

# Sort recursively by file size
gdls <TARGET> -R -f "id,name,size" -s "size desc"

# Display/Output JSON
gdls <TARGET> -R -F json

# Write CSV to a file
gdls <TARGET> -lR -o drive_data.csv -O csv

# Find PDFs in a hierarchy
gdls <TARGET> -R -q | grep "\.pdf$"

# Get aggregate statistics for a single folder:
gdls <TARGET> -i -f "id,name,totalSize,itemCount"
```

### Output

When connected to a terminal, `gdls` displays an auto-aligned table. When output is redirected or piped, it emits TSV-formatted records by default.

```bash
gdls <TARGET> -l
gdls <TARGET> -l > files.tsv
gdls <TARGET> -F csv
gdls <TARGET> -F json
```

Use `--no-header` to omit the header row.

### Fields

Standard Google Drive API fields can be selected with `--fields`.

`gdls` also provides fields derived from the Drive hierarchy:

| Field | Description |
| --- | --- |
| `permissions` | `ls`-style metadata as permission/type string (e.g. `drw-+` / `-rw--`) |
| `relativePath` | Relative path from the target folder |
| `relativeIdPath` | Relative ID path from the target folder |
| `depth` | Depth from the target root |
| `itemCount` | Number of descendant items |
| `fileCount` | Number of descendant files |
| `folderCount` | Number of descendant folders |
| `childItemCount` | Number of direct child items |
| `childFileCount` | Number of direct child files |
| `childFolderCount` | Number of direct child folders |
| `totalSize` | Sum of descendant file sizes |
| `totalQuotaBytesUsed` | Sum of descendant quota usage |
| `oldestCreatedTime` | Oldest creation time among descendants |
| `latestCreatedTime` | Latest creation time among descendants |
| `oldestModifiedTime` | Oldest modification time among descendants |
| `latestModifiedTime` | Latest modification time among descendants |

Fields requiring descendant aggregation automatically trigger recursive scanning.

## Command-line options

| Option | Description |
| --- | --- |
| `target` | Google Drive file/folder URL or ID |
| `-R`, `--recursive` | Recursively list descendants |
| `-t`, `--include-trashed` | Include trashed items |
| `-d`, `--depth` | Maximum recursion depth |
| `-i`, `--item` | Fetch only the target item |
| `-D`, `--describe` | Show a detailed description of a single item |
| `-l`, `--long` | Use the default long-format fields |
| `-f`, `--fields` | Comma-separated output fields |
| `-s`, `--sort` | Sort by one or more fields |
| `-F`, `--format` | Output format: `auto`, `table`, `tsv`, `csv`, `json` |
| `-H`, `--no-header` | Suppress TSV/CSV headers |
| `-o`, `--output` | Write results to a file |
| `-O`, `--output-format` | File format: `tsv`, `csv`, `json` |
| `-a`, `--append` | Append to an existing output file |
| `--log-level` | Log verbosity |
| `-q`, `--quiet` | Suppress progress display and non-error logs |
| `--client-secret` | Path to `client_secret.json` |
| `--token-file` | Path to `token.json` |
| `-h`, `--help` | Show help |

## Troubleshooting

### `command not found`

Ensure that the Python script directory is included in `PATH`.

- Windows: `C:\Users\<User>\AppData\Local\Programs\Python\Python3x\Scripts` or `%APPDATA%\Python\Python3x\Scripts`
- Linux / macOS: `~/.local/bin`

### Missing client secret

Ensure that `client_secret.json` is in the appropriate directory, or specify its location explicitly:

```bash
gdls <TARGET> --client-secret /path/to/client_secret.json
```

### Authentication errors

Remove the cached OAuth token and authenticate again.

**Windows:**

```powershell
Remove-Item "$env:LOCALAPPDATA\SnowyTools\gdls\token.json" -ErrorAction SilentlyContinue
```

**macOS / Linux:**

```bash
rm -f ~/.local/share/SnowyTools/gdls/token.json
```

### Folder not found

Check that:

- the folder ID or URL is correct
- your Google account has access to the folder
- the item is not in the trash, or use `--include-trashed` if necessary

### Slow recursive scans

Recursive scans are required for descendant-based fields such as `totalSize` and `itemCount`.

For large folders:

- avoid recursive scanning when it is not needed
- Avoid aggregate fields such as `totalSize` or `oldestCreatedTime` when they are not needed

## License

BSD 3-Clause
