# gdls: Google Drive List

A command-line tool that lists Google Drive folder structures in an `ls`-like format.

It displays file and folder names, sizes, modification dates, owners, and more. It supports TSV/CSV/JSON output on stdout or files, recursive exploration, shared drives, and aggregated directory metrics.

## Features

- Hierarchical exploration of Google Drive folders
- Support for both standard Google Drives and Shared Drives
- Flexible selection of file/folder output fields
- Recursive item enumeration and data aggregation
- Export to TSV, CSV and JSON formats
- Sorting capabilities for output results
- Auto-aligned table format and color-coded output in terminal
- Pipeline-friendly standard output (for `stdout`)
- Read-only access via Google OAuth 2.0
- Automatic OAuth token refresh

## Requirements

- Python 3.10+
- Google Drive API enabled
- Dependencies managed in `pyproject.toml`
  - progress-reporters (https://github.com/snowflake36u/progress-reporters.git)
  - and others

## Installation

For local development / editable install:

```bash
git clone https://github.com/snowflake36u/gdls
cd gdls
python -m pip install -e .
```

Once published to PyPI, installation will also be:

```bash
pip install gdls
```

After installation, the command is available anywhere:

```bash
gdls --help
```

## Authentication

OAuth 2.0 credentials are required to interact with the Google Drive API.

1. Create a project in the Google Cloud Console.
2. Enable the **Google Drive API**.
3. Create an **OAuth 2.0 Client ID** (Application type: **Desktop app**).
4. Download `client_secret.json`.
5. Save the file to one of the following locations:

| OS            | Location                                      |
| ------------- | --------------------------------------------- |
| Windows       | `%APPDATA%\SnowyTools\GDLS\client_secret.json` |
| macOS / Linux | `~/.config/SnowyTools/GDLS/client_secret.json` |

On the first run, a browser window will open prompting you to authenticate. A `token.json` file will automatically be saved in the directory specified above.

You can also customize the credential paths using the `--client-secret` and `--token-file` options, or their corresponding environment variables.

## Usage

### Basic Usage

```bash
gdls <FOLDER_ID>
```

Lists items **directly inside** the specified folder.

You can specify a folder ID, a Google Drive folder URL, or a file preview URL:

```bash
gdls 1ABC123xyzABC123xyzABC123xyz
gdls https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
gdls root
gdls /
```

By default, item names are output in TSV format on stdout. If `-o/--output` is specified without `-O/--output-format`, the file is written as TSV by default.

### Common Options

```bash
# Display detailed information (permissions, owners, size, modifiedTime, id, name)
gdls <FOLDER_ID> -l

# Display information about the folder itself
gdls <FOLDER_ID> -i

# Display detailed info in human-readable format
gdls <FOLDER_ID> -d

# Specify output fields
gdls <FOLDER_ID> -f "id,name,size,createdTime,modifiedTime"

# Recursively list files and folders
gdls <FOLDER_ID> -R

# Output to stdout in JSON format
gdls <FOLDER_ID> -R -F json

# Output to a file in JSON format
gdls <FOLDER_ID> -R -o drive_data.json -O json

# Output to a file
gdls <FOLDER_ID> -l -o output.tsv

# Append to an existing file
gdls <FOLDER_ID_1> -l -o combined.tsv
gdls <FOLDER_ID_2> -l -o combined.tsv --append --no-header

# Sort results (use ' desc' suffix for descending order)
gdls <FOLDER_ID> -R -f "name,size" -S "size desc"

# Pipe processing (use -q / --quiet to suppress logs and progress)
gdls <FOLDER_ID> -R -q | grep "\.pdf$"
```

## Output Formats

### TSV

TSV is the default output format on stdout and for `-o/--output` files when no explicit file format is requested.

When printed directly to the terminal, output is rendered as an auto-aligned table. When redirected to a file or passed through a pipe, it automatically outputs raw tab-separated values.

```bash
gdls <FOLDER_ID> -l
gdls <FOLDER_ID> -l > files.tsv
```

Use `--no-header` to omit the column header row.

### CSV and JSON

```bash
# CSV on stdout
gdls <FOLDER_ID> -F csv

# CSV written to a file
gdls <FOLDER_ID> -o drive_data.csv -O csv

# JSON on stdout
gdls <FOLDER_ID> -F json

# JSON written to a file
gdls <FOLDER_ID> -o drive_data.json -O json
```

These formats are ideal for programmatic data processing and analysis.

### Terminal Display

When output directly to the terminal, this application provides the following enhancements:

- Color-coded output to distinguish files, folders, and shortcuts
- Column alignment support for full-width characters (e.g., Japanese)
- Real-time progress bar during recursive scanning

Color formatting is automatically disabled when output is piped or redirected to a file.

## Custom Output Fields

In addition to standard Google Drive API fields, `--fields` supports the following custom fields:

| Field                 | Description |
| --------------------- | ------------------------------------------------ |
| `permissions` | `ls`-style file type and permission representation (e.g. `lrwx+`, `-rw-+`) |
| `relativePath`        | Relative path from the target folder (Be aware that this is not a unique signature, as duplicate item names are allowed) |
| `depth`               | Directory nesting depth |
| `itemCount`           | Number of descendant items |
| `fileCount`           | Number of descendant files |
| `folderCount`         | Number of descendant folders |
| `childItemCount`      | Number of child items |
| `childFileCount`      | Number of child files |
| `childFolderCount`    | Number of child folders |
| `totalSize`           | Total size including all descendant items |
| `totalQuotaBytesUsed` | Total quota used including all descendant items |
| `oldestCreatedTime`   | Oldest creation date among descendant items |

Specifying aggregated fields (`totalSize`, `totalQuotaBytesUsed`, `oldestCreatedTime`) automatically triggers recursive exploration.

## Command-Line Options

| Option              | Description                                                |
| ------------------- | ---------------------------------------------------------- |
| `target`            | Google Drive folder URL or ID                             |
| `-R`, `--recursive` | Recursively list descendant items                          |
| `-t`, `--include-trashed` | Include items in the trash                                 |
| `-d`, `--depth` | Maximum depth of recursion when listing subfolders |
| `-i`, `--item`      | Target the specified item/folder itself                    |
| `-D`, `--describe`  | Display detailed item metadata in human-readable format    |
| `-l`, `--long`      | Display standard attributes in long format                 |
| `-f`, `--fields`    | Specify output fields                                      |
| `-s`, `--sort`      | Sort output by specified fields                            |
| `-F`, `--format`    | Console output format (`auto`, `table`, `tsv`, `csv`, `json`) |
| `-H`, `--no-header` | Omit header row in TSV/CSV output                         |
| `-o`, `--output`    | Output file path                                           |
| `-O`, `--output-format` | File format for `--output` (`tsv`, `csv`, `json`; default: `tsv`) |
| `-a`, `--append`    | Append output to an existing file                          |
| `--log-level`       | Set logging verbosity level                                |
| `-q`, `--quiet`     | Suppress progress bar and non-error logs                  |
| `--client-secret`   | Path to `client_secret.json`                               |
| `--token-file`      | Path to `token.json`                                       |
| `-h`, `--help`      | Show help message                                          |

## Examples

### Get total size within a folder

```bash
gdls <FOLDER_ID> -i -f "id,name,totalSize"
```

## Environment Variables

You can define custom paths for authentication files using environment variables.

### Windows PowerShell

```powershell
$env:GDLS_CLIENT_SECRET_FILE="C:\custom\path\client_secret.json"
$env:GDLS_TOKEN_FILE="C:\custom\path\token.json"

gdls <FOLDER_ID>
```

### macOS / Linux

```bash
export GDLS_CLIENT_SECRET_FILE="/custom/path/client_secret.json"
export GDLS_TOKEN_FILE="/custom/path/token.json"

gdls <FOLDER_ID>
```

Command-line options (`--client-secret`, `--token-file`) take precedence over environment variables.

## Troubleshooting

### "command not found" or Command Not Recognized

If `gdls` is not recognized as a command after installation, ensure that your Python script directory is included in your system's `PATH` environment variable.

- **Windows**: `C:\Users\<User>\AppData\Local\Programs\Python\Python3x\Scripts\` (or `%APPDATA%\Python\Python3x\Scripts`)
- **Linux / macOS**: `~/.local/bin`

### Missing Client Secret File

Ensure `client_secret.json` is located in the appropriate directory.

Alternatively, explicitly pass the path using the CLI option:

```bash
gdls <FOLDER_ID> --client-secret /path/to/client_secret.json
```

### Authentication Errors (e.g., `invalid_grant`)

Delete your stored credentials and re-authenticate.

**Windows**:

```powershell
Remove-Item "$env:APPDATA\SnowyTools\GDLS\token.json" -ErrorAction SilentlyContinue
```

**macOS / Linux**:

```bash
rm -f ~/.config/SnowyTools/GDLS/token.json
```

Then run `gdls` again.

### Folder not Found

Please check the following:

- Verify the folder ID or URL is accurate.
- Confirm your authenticated Google account has access permissions to the folder.
- Ensure the folder is not in the trash (use `--include-trashed` to include trashed items).

### Slow Performance on Large Folders

- Disable recursive scanning:
  - Avoid using `--recursive`.
  - Avoid specifying aggregation fields in `--fields` (`totalSize`, `oldestCreatedTime`, etc.).
- Suppress terminal progress rendering with `--quiet`.

## License

BSD 3-Clause
