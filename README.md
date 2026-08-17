# gdls: Google Drive List

A command-line tool that lists Google Drive folder structures in an `ls`-like format.

It displays file and folder names, sizes, modification dates, owners, and more. It supports output in TSV or JSON format, recursive exploration, shared drives, and aggregated directory metrics.

## Features

- Hierarchical exploration of Google Drive folders
- Support for both standard Google Drives and Shared Drives
- Flexible selection of file/folder output fields
- Recursive item enumeration and data aggregation
- Export to TSV and JSON formats
- Sorting capabilities for output results
- Auto-aligned table format and color-coded output in terminal
- Pipeline-friendly standard output (for `stdout`)
- Read-only access via Google OAuth 2.0
- Automatic OAuth token refresh

## Requirements

- Python 3.10+
- Google Drive API enabled
- Dependencies listed in `requirements.txt`

## Installation

```bash
git clone https://github.com/snowflake36u/gdls
cd gdls
pip install -r requirements.txt
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
python gdls.py <FOLDER_ID>
```

Lists items **directly inside** the specified folder.

You can specify a folder ID, a Google Drive folder URL, or a file preview URL:

```bash
python gdls.py 1ABC123xyzABC123xyzABC123xyz
python gdls.py https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
python gdls.py root
python gdls.py /
```

By default, item names are output in TSV format.

### Common Options

```bash
# Display detailed information (permissions, owners, size, modifiedTime, id, name)
python gdls.py <FOLDER_ID> -l

# Display information about the folder itself
python gdls.py <FOLDER_ID> -i

# Display detailed info in human-readable format
python gdls.py <FOLDER_ID> -d

# Specify output fields
python gdls.py <FOLDER_ID> -f "id,name,size,createdTime,modifiedTime"

# Recursively list files and folders
python gdls.py <FOLDER_ID> -R

# Output in JSON format
python gdls.py <FOLDER_ID> -j

# Output to a file
python gdls.py <FOLDER_ID> -l -o output.tsv

# Append to an existing file
python gdls.py <FOLDER_ID_1> -l -o combined.tsv
python gdls.py <FOLDER_ID_2> -l -o combined.tsv --append --no-header

# Sort results (use ' desc' suffix for descending order)
python gdls.py <FOLDER_ID> -R -f "name,size" -S "size desc"

# Pipe processing (use -q / --quiet to suppress logs and progress)
python gdls.py <FOLDER_ID> -R -q | grep "\.pdf$"
```

## Output Formats

### TSV

TSV is the default output format.

When printed directly to the terminal, output is rendered as an auto-aligned table. When redirected to a file or passed through a pipe, it automatically outputs raw tab-separated values.

```bash
python gdls.py <FOLDER_ID> -l
python gdls.py <FOLDER_ID> -l > files.tsv
```

Use `--no-header` to omit the column header row.

### JSON (`-j` / `--json`)

```bash
python gdls.py <FOLDER_ID> -Rj -o drive_data.json
```

Ideal for programmatic data processing and analysis.

### Terminal Display

When output directly to the terminal, this application provides the following enhancements:

- Color-coded output to distinguish files, folders, and shortcuts
- Column alignment support for full-width characters (e.g., Japanese)
- Real-time progress bar during recursive scanning

Color formatting is automatically disabled when output is piped or redirected to a file.

## Custom Output Fields

In addition to standard Google Drive API fields, `--fields` supports the following custom fields:

| Field                 | Description                                      |
| --------------------- | ------------------------------------------------ |
| `permissions`         | `ls`-style file type and permission representation (e.g. `lrwx+`, `-rw-+`) |
| `relativePath`        | Relative path from the root folder              |
| `depth`               | Directory nesting depth                          |
| `totalSize`           | Total size including all descendant items        |
| `totalQuotaBytesUsed` | Total quota used including all descendant items  |
| `oldestCreatedTime`   | Oldest creation date among descendant items      |

Specifying aggregated fields (`totalSize`, `totalQuotaBytesUsed`, `oldestCreatedTime`) automatically triggers recursive exploration.

## Command-Line Options

| Option              | Description                                                |
| ------------------- | ---------------------------------------------------------- |
| `target`            | Google Drive folder URL or ID                             |
| `-R`, `--recursive` | Recursively list descendant items                          |
| `--include-trashed` | Include items in the trash                                 |
| `-i`, `--item`      | Target the specified item/folder itself                    |
| `-d`, `--describe`  | Display detailed item metadata in human-readable format    |
| `-l`, `--long`      | Display standard attributes in long format                 |
| `-f`, `--fields`    | Specify output fields                                      |
| `-S`, `--sort`      | Sort output by specified fields                            |
| `-o`, `--output`    | Output file path                                           |
| `-a`, `--append`    | Append output to an existing file                          |
| `-j`, `--json`      | Output in JSON format                                      |
| `--no-header`       | Omit header row in TSV output                              |
| `--log-level`       | Set logging verbosity level                                |
| `-q`, `--quiet`     | Suppress progress bar and non-error logs                  |
| `--client-secret`   | Path to `client_secret.json`                               |
| `--token-file`      | Path to `token.json`                                       |
| `-h`, `--help`      | Show help message                                          |

## Examples

### Get total size within a folder

```bash
python gdls.py <FOLDER_ID> -i -f "id,name,totalSize"
```

## Environment Variables

You can define custom paths for authentication files using environment variables.

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

### Missing Client Secret File

Ensure `client_secret.json` is located in the appropriate directory.

Alternatively, explicitly pass the path using the CLI option:

```bash
python gdls.py <FOLDER_ID> --client-secret /path/to/client_secret.json
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

Then run `gdls.py` again.

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
