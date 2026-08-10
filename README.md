# GoogleDriveLister

Google Drive のフォルダ構造を TSV ファイルにエクスポートするツールです。フォルダのサイズ、更新日時、所有者情報などを一覧化できます。

## 主な機能

- Google Drive フォルダの全階層をスキャン
- ファイル/フォルダ情報をTSV形式で出力
- サイズ・作成日時・更新日時などを自動集計（子孫要素を含む）
- Google OAuth2 による安全な読み取り専用アクセス

## 必要要件

- Python 3.7 以上
- Google Drive API アクセス権限

## 使い方

### 1. インストール

```bash
git clone https://github.com/snowflake36u/google-drive-lister
cd google-drive-lister
pip install -r requirements.txt
```

### 2. Google API 認証情報の取得と配置

1. [Google Cloud Console](https://console.cloud.google.com/) にアクセスし、新しいプロジェクトを作成します。
2. **Google Drive API** を有効化します。
3. **OAuth 2.0 認証情報**（デスクトップアプリケーション）を作成し、`client_secret.json` をダウンロードします。
4. ダウンロードしたファイルを、お使いのOSに合わせて以下のディレクトリに配置してください（ディレクトリが存在しない場合は手動で作成してください）。

- **Windows**: `%APPDATA%\SnowyTools\GoogleDriveLister\client_secret.json`
- **macOS/Linux**: `~/.config/SnowyTools/GoogleDriveLister/client_secret.json`

※ 初回実行時に認証が完了すると、上記と同じディレクトリに `token.json` が自動生成されます。

### 3. 実行

`<YOUR_FOLDER_ID>` を該当するドライブフォルダのIDで置き換えてください。

```bash
cd src
python summary_folder.py <YOUR_FOLDER_ID>"
```

または

```bash
cd src
python summary_folder.py https://drive.google.com/drive/folders/<YOUR_FOLDER_ID>"
```

※ 実行したディレクトリに `drive_contents.tsv` という名前で結果が出力されます。

## 出力データ仕様

デフォルトで以下の列がTSV形式で出力されます。

| 列名 | 説明 |
|------|------|
| id | Google Drive ファイルID |
| webViewLink | Google Drive リンク |
| name | アイテム名 |
| mimeType | ファイルタイプ |
| description | アイテムの説明 |
| owners | 所有者名 |
| modifiedTime | 更新日時 |
| viewedByMeTime | 最終閲覧日時 |
| createdTime | 作成日時 |
| oldestDescendantCreationTime | 子孫アイテムのうち最古の作成日時 |
| size | ファイル/フォルダサイズ（バイト単位。子孫を含む） |
| quotaBytesUsed | 容量使用量（バイト単位。子孫を含む） |

**注**: フォルダの `size` と `quotaBytesUsed` は、そのフォルダ配下にある全子孫要素の合計値として計算されます。

## カスタマイズ（環境変数）

環境変数を使用することで、認証ファイルの読み込み場所を変更できます（出力ファイルは常に実行場所になります）。

**Windows PowerShell:**
```powershell
$env:SNOWY_GDL_CLIENT_SECRET_FILE="C:\custom\path\client_secret.json"
$env:SNOWY_GDL_TOKEN_FILE="C:\custom\path\token.json"
```

**macOS/Linux:**
```bash
export SNOWY_GDL_CLIENT_SECRET_FILE="/custom/path/client_secret.json"
export SNOWY_GDL_TOKEN_FILE="/custom/path/token.json"
```

## トラブルシューティング

### "Client Secret file not found" エラー
`client_secret.json` が正しい場所に配置されているか確認してください。

- **Windows**: `Get-Item -Path "$env:APPDATA\SnowyTools\GoogleDriveLister\client_secret.json"`
- **macOS/Linux**: `ls -la ~/.config/SnowyTools/GoogleDriveLister/`

### 認証に失敗する場合
Google Cloud Console の OAuth 同意画面の設定を確認した上で、既存の `token.json` を削除し、初回認証からやり直してください。

- **Windows**: `Remove-Item "$env:APPDATA\SnowyTools\GoogleDriveLister\token.json"`
- **macOS/Linux**: `rm ~/.config/SnowyTools/GoogleDriveLister/token.json`

## ライセンス

このプロジェクトは BSD 3-Clause の下で公開されています。