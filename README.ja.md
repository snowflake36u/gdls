# gdls: Google Drive List

Google Drive のフォルダ構造を `ls` コマンド風に一覧表示するコマンドラインツールです。

ファイル・フォルダの名前やサイズ、更新日時、所有者などを一覧表示でき、TSV / JSON 形式での出力、再帰的な探索、共有ドライブ、集計情報の取得にも対応しています。

## Features

- Google Drive のフォルダを階層的に探索
- 通常のドライブおよび共有ドライブに対応
- ファイル・フォルダの属性を自由に指定して出力
- フォルダ配下アイテムの再帰的列挙とデータの集計
- TSV / JSON 形式でのエクスポート
- 出力結果のソート
- ターミナルでの見やすい表形式・カラー表示
- パイプライン処理を考慮した標準出力
- Google OAuth 2.0 による読み取り専用アクセス
- OAuth トークンの自動更新

## Requirements

- Python 3.10+
- Google Drive API
- `requirements.txt` に記載された依存パッケージ

## Installation

```bash
git clone https://github.com/snowflake36u/gdls
cd gdls
pip install -r requirements.txt
```

## Authentication

Google Drive API を利用するため、OAuth 2.0 の認証情報が必要です。

1. Google Cloud Console でプロジェクトを作成
2. **Google Drive API** を有効化
3. **OAuth 2.0 クライアント ID（デスクトップアプリ）**を作成
4. `client_secret.json` をダウンロード
5. 以下の場所に配置

| OS            | 配置先                                            |
| ------------- | ---------------------------------------------- |
| Windows       | `%APPDATA%\SnowyTools\GDLS\client_secret.json` |
| macOS / Linux | `~/.config/SnowyTools/GDLS/client_secret.json` |

初回実行時にブラウザで Google アカウントの認証を行うと、同じディレクトリに `token.json` が自動生成されます。

認証ファイルの場所は `--client-secret` / `--token-file` オプションまたは対応する環境変数でも変更できます。

## Usage

### 基本

```bash
python gdls.py <FOLDER_ID>
```

指定したフォルダの**直下にあるアイテム**を一覧表示します。

フォルダ ID のほか、Google Drive のフォルダ URL やファイルプレビュー URLも指定できます。

```bash
python gdls.py 1ABC123xyzABC123xyzABC123xyz
python gdls.py https://drive.google.com/drive/folders/1ABC123xyzABC123xyzABC123xyz
python gdls.py root
python gdls.py /
```

デフォルトでは、アイテム名を TSV として出力します。

### よく使うオプション

```bash
# 詳細情報を表示 (permissions、owners、size、modifiedTime、id、name)
python gdls.py <FOLDER_ID> -l

# フォルダ自身の情報を表示
python gdls.py <FOLDER_ID> -i

# 人間向けの行形式表示
python gdls.py <FOLDER_ID> -d

# 出力フィールドの指定
python gdls.py <FOLDER_ID> -f "id,name,size,createdTime,modifiedTime"

# 再帰的にファイル・フォルダを列挙
python gdls.py <FOLDER_ID> -R

# JSON形式で出力
python gdls.py <FOLDER_ID> -j

# ファイルに出力
python gdls.py <FOLDER_ID> -l -o output.tsv

# 既存ファイルへの追記
python gdls.py <FOLDER_ID_1> -l -o combined.tsv
python gdls.py <FOLDER_ID_2> -l -o combined.tsv --append  --no-header

# ソート (' desc'接尾辞で降順)
python gdls.py <FOLDER_ID> -R -f "name,size" -S "size desc"

# パイプ処理 (`-q` / `--quiet` でメッセージ・ログ抑制)
python gdls.py <FOLDER_ID> -R -q | grep "\.pdf$"
```

## Output

### TSV

デフォルトの出力形式は TSV です。

ターミナルでは列幅を自動調整した表形式で表示され、ファイルへのリダイレクトやパイプ処理ではタブ区切りの TSV として扱われます。

```bash
python gdls.py <FOLDER_ID> -l
python gdls.py <FOLDER_ID> -l > files.tsv
```

`--no-header` を指定すると TSV のヘッダーを省略できます。

### JSON (`-j` / `--json` オプション)

```bash
python gdls.py <FOLDER_ID> -Rj -o drive_data.json
```

データ処理や分析など、機械的に扱う場合に適しています。

### Terminal output

ターミナルに直接出力する場合は、以下の機能が利用できます。

- ファイル、フォルダ、ショートカットを区別するカラー表示
- 日本語などの全角文字を考慮した列揃え
- 再帰探索時のプログレスバー

パイプやファイルリダイレクト時には、カラー表示は自動的に無効になります。

## Output fields

`--fields` では Google Drive API の標準フィールドに加えて、以下の独自フィールドを利用できます。

| Field                 | Description           |
| --------------------- | --------------------- |
| `permissions`         | `ls` 風のファイルタイプ・権限表示   |
| `relativePath`        | ルートからの相対パス            |
| `depth`               | フォルダ階層の深さ             |
| `totalSize`           | 子孫アイテムを含むサイズ合計        |
| `totalQuotaBytesUsed` | 子孫アイテムを含む容量使用量        |
| `oldestCreatedTime`   | 子孫アイテムを含む最古の作成日時      |

`totalSize`、`totalQuotaBytesUsed`、`oldestCreatedTime` などの集計フィールドを指定すると、必要に応じて自動的に子孫アイテムが探索されます。

## Command-line options

| Option              | Description                   |
| ------------------- | ----------------------------- |
| `target`            | Google Drive のフォルダ URL または ID |
| `-R`, `--recursive` | 子孫アイテムを再帰的に取得                 |
| `--include-trashed` | ゴミ箱内のアイテムを含める                 |
| `-i`, `--item`      | 指定アイテム自身を取得                   |
| `-d`, `--describe`        | アイテムの詳細情報を人間向けに表示             |
| `-l`, `--long`      | 基本属性を長形式で表示                   |
| `-f`, `--fields`    | 出力フィールドを指定                    |
| `-S`, `--sort`            | 出力をソート                        |
| `-o`, `--output`    | 出力ファイルを指定                     |
| `-a`, `--append`    | 既存の出力ファイルに追記                  |
| `-j`, `--json`            | JSON 形式で出力                    |
| `--no-header`       | TSV のヘッダーを省略                  |
| `--log-level`       | ログレベルを指定                      |
| `-q`, `--quiet`     | 進捗表示・通常ログを抑制                  |
| `--client-secret`   | `client_secret.json` のパスを指定   |
| `--token-file`      | `token.json` のパスを指定           |
| `-h`, `--help`      | ヘルプを表示                        |

## Examples

### フォルダの合計サイズを取得

```bash
python gdls.py <FOLDER_ID> -i -f "id,name,totalSize"
```


## Environment variables

認証ファイルの場所は環境変数でも指定できます。

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

コマンドラインオプション (`--client-secret, --token-file`) で指定したパスが環境変数より優先されます。

## Troubleshooting

### `Client Secret file not found`

`client_secret.json` が所定の場所にあるか確認してください。

コマンドライン引数でパスの直接指定もできます。

```bash
python gdls.py <FOLDER_ID> --client-secret /path/to/client_secret.json
```

### `invalid_grant` などの認証エラー

既存の `token.json` を削除したうえで再認証してください。

**Windows**:

```powershell
Remove-Item "$env:APPDATA\SnowyTools\GDLS\token.json" -ErrorAction SilentlyContinue
```

**macOS / Linux**:

```bash
rm -f ~/.config/SnowyTools/GDLS/token.json
```

その後、再度 `gdls.py` を実行してください。

### フォルダが見つからない

以下を確認してください。

- フォルダ ID または URL が正しいか
- 認証した Google アカウントにアクセス権があるか
- 対象フォルダがゴミ箱に入っていないか
  - ゴミ箱内アイテムを表示したい場合は `--include-trashed` を使用する

### 大規模なフォルダで時間がかかる場合

- 再帰的探索を無効化する
  - `--recursive` を使用しない
  - `--fields` に集計フィールドを指定しない (`totalSize`、`oldestCreatedTime` など)
- `--quiet` により進捗表示を無効化する

## License

BSD 3-Clause

