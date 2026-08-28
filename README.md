# gdls: Google Drive List

`gdls` is a command-line tool that lists contents of a Google Drive folder in an `ls`-like format.

It can list file and folder names, and other fields based on API such as size and modification time, and supports TSV/CSV/JSON output to stdout or files. It also supports recursive traversal, aggregating statistics, and sorting.

## Features

- List the direct children of a Google Drive folder or a single target item
- Traverse subfolders recursively with optional depth limits
- Compute aggregated statistics such as `totalSize`, `itemCount`, and `oldestCreatedTime`
- Export results to TSV, CSV, or JSON
- Sort output by one or more fields
- Render terminal output as a compact table when stdout is a TTY
- Display detailed single-item metadata in a readable format
- Use read-only Google OAuth 2.0 credentials
- Refresh expired tokens automatically

## Requirements

- Python 3.10+
- A Google Cloud project with the Google Drive API enabled
- Dependency installation via `pyproject.toml`
  - `google-api-python-client`
  - `google-auth-oauthlib`
  - `tqdm`
  - `platformdirs`
  - `progress-reporters`

## Installation

For local development / editable install:

```bash
git clone https://github.com/snowflake36u/gdls
cd gdls
python -m pip install -e .
```

**(Not supported yet)** If published to PyPI, installation is also:

```bash
pip install gdls
```

After installation, the command is available from the shell:

```bash
gdls --help
```

## Authentication

OAuth 2.0 credentials are required to access the Google Drive API.

1. Create a project in the Google Cloud Console.
2. Enable the Google Drive API.
3. Create an OAuth 2.0 client ID for a desktop app.
4. Download `client_secret.json`.
5. Place it in the default app-data directory, or pass it explicitly with `--client-secret`.

| OS            | Location                                      |
| ------------- | --------------------------------------------- |
| Windows       | `LOCALAPPDATA\SnowyTools\gdls\token.json` |
| macOS / Linux | `~/.local/share/SnowyTools/gdls/token.json` |

On the first run, a browser window opens for authentication and a `token.json` file is created automatically in the same user-data directory.

You can override the default file locations at runtime with:

```bash
gdls <TARGET> --client-secret /path/to/client_secret.json --token-file /path/to/token.json
```

## Usage

### Basic usage

```bash
gdls <TARGET>
```

This lists the items directly under the specified folder.

The target can be a folder ID, a Google Drive folder URL, a file URL, `root`, or `/`:

```bash
gdls 1ABC123xyzABC123xyzABC123xyz
gdls https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
gdls https://drive.google.com/file/d/FILE_ID/view?usp=sharing
gdls root
gdls /
```

By default, result is output to stdout in TSV format when not attached to a TTY. When `-o/--output` is used without `-O/--output-format`, the file is written as TSV.

### Common options

```bash
# Show the default long-format columns
# (permissions, owners, size, modifiedTime, id, name)
gdls <TARGET> -l

# Show information for the target item itself
gdls <TARGET> -i

# Show detailed metadata for a single item in a readable format
gdls <TARGET> -D

# Limit recursion depth to 2 levels
gdls <TARGET> -R -d 2

# Specify output fields
gdls <TARGET> -f "id,name,size,createdTime,modifiedTime"

# Recursively list files and folders
gdls <TARGET> -R

# Output to stdout in JSON format
gdls <TARGET> -R -F json

# Output to a file in JSON format
gdls <TARGET> -R -o drive_data.json -O json

# Output to a file
gdls <TARGET> -l -o output.tsv

# Append to an existing file
gdls <TARGET_1> -l -o combined.tsv
gdls <TARGET_2> -l -o combined.tsv --append --no-header

# Sort results (use ' desc' for descending order)
gdls <TARGET> -R -f "name,size" -s "size desc"

# Pipe processing while suppressing logs/progress
gdls <TARGET> -R -q | grep "\.pdf$"
```

## Output formats

### TSV

TSV is the default format for stdout when output is not a TTY, and it is also the default file format for `-o/--output` when no explicit file format is requested.

When written directly to a terminal, the output is rendered as an auto-aligned table. When redirected to a file or piped, it emits raw tab-separated records.

```bash
gdls <TARGET> -l
gdls <TARGET> -l > files.tsv
```

Use `--no-header` to omit the column header row.

### CSV and JSON

```bash
# CSV to stdout
gdls <TARGET> -F csv

# CSV to a file
gdls <TARGET> -o drive_data.csv -O csv

# JSON to stdout
gdls <TARGET> -F json

# JSON to a file
gdls <TARGET> -o drive_data.json -O json
```

These formats are useful for downstream processing, filtering, or importing into other tools.

### Terminal display

When output is sent to a terminal, `gdls` provides:

- a compact auto-aligned table
- folder and shortcut highlighting in color
- a progress bar during recursive scanning

Color formatting is automatically disabled when output is redirected to a file or piped.

## Custom output fields

In addition to standard Google Drive API fields, the following custom fields are supported by `--fields`:

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
| `oldestCreatedTime` | Oldest creation time among descendant items |
| `latestCreatedTime` | Latest creation time among descendant items |
| `oldestModifiedTime` | Oldest modified time among descendant items |
| `latestModifiedTime` | Latest modified time among descendant items |

Fields that require descendant aggregation (`totalSize`, `itemCount`, `oldestCreatedTime`, etc.) automatically cause recursive scanning to run.

## Command-line options

| Option | Description |
| --- | --- |
| `target` | Google Drive file/folder URL or ID |
| `-R`, `--recursive` | Recursively list descendant items |
| `-t`, `--include-trashed` | Include trashed items in the results |
| `-d`, `--depth` | Maximum depth when recursively scanning |
| `-i`, `--item` | Fetch only the target item itself |
| `-D`, `--describe` | Show a detailed human-readable description of a single item |
| `-l`, `--long` | Use the default long-format field set |
| `-f`, `--fields` | Comma-separated list of output fields |
| `-s`, `--sort` | Sort output by one or more keys, e.g. `size desc, name` |
| `-F`, `--format` | Console output format: `auto`, `table`, `tsv`, `csv`, `json` |
| `-H`, `--no-header` | Suppress the header row for TSV/CSV output |
| `-o`, `--output` | Write results to a file |
| `-O`, `--output-format` | File format when using `--output`: `tsv`, `csv`, `json` |
| `-a`, `--append` | Append to an existing output file |
| `--log-level` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `-q`, `--quiet` | Suppress progress bars and non-error log messages |
| `--client-secret` | Explicit path to `client_secret.json` |
| `--token-file` | Explicit path to `token.json` |
| `-h`, `--help` | Show the help message |

## Examples

### Get total size for a folder

```bash
gdls <TARGET> -i -f "id,name,totalSize"
```

### Show a detailed single-item view

```bash
gdls <TARGET> -D
```

## Troubleshooting

### "command not found" or command is not recognized

If `gdls` is not recognized after installation, ensure the Python script directory is present in `PATH`.

- Windows: `C:\Users\<User>\AppData\Local\Programs\Python\Python3x\Scripts` or `%APPDATA%\Python\Python3x\Scripts`
- Linux / macOS: `~/.local/bin`

### Missing client secret file

Ensure `client_secret.json` is located in the appropriate directory.

Alternatively, explicitly pass the path using the CLI option:

```bash
gdls <TARGET> --client-secret /path/to/client_secret.json
```

### Authentication errors

If token refresh or consent fails, remove the cached `token.json` and re-run `gdls`.

**Windows**:

```powershell
Remove-Item "$env:LOCALAPPDATA\SnowyTools\gdls\token.json" -ErrorAction SilentlyContinue
```

**macOS / Linux**:

```bash
rm -f ~/.local/share/SnowyTools/gdls/token.json
```

### Folder not found

Check the following:

- the folder ID or URL is correct
- your Google account has access to the folder
- the item is not trashed; use `--include-trashed` if needed

### Slow performance on large folders

- Avoid recursive scanning unless necessary
- Avoid aggregative fields such as `totalSize` or `oldestCreatedTime` when not needed
- Use `--quiet` to suppress progress display

## License

BSD 3-Clause
