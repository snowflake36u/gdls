import os
from pathlib import Path
import platform

APP_NAME = "GoogleDriveLister"

def get_app_data_dir():
    """OS別のアプリデータディレクトリを取得"""
    system = platform.system()
    if system == "Windows":
        # Windows: %APPDATA%\SnowyTools\<APP_NAME>
        app_data = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif system == "Darwin":
        # macOS: ~/.config/SnowyTools/<APP_NAME>
        app_data = Path.home() / ".config"
    else:
        # Linux: ~/.config/SnowyTools/<APP_NAME>
        app_data = Path.home() / ".config"
    
    return app_data / "SnowyTools" / APP_NAME

# デフォルトパス
APP_DATA_DIR = get_app_data_dir()

CLIENT_SECRET_FILE = APP_DATA_DIR / "client_secret.json"
TOKEN_FILE = APP_DATA_DIR / "token.json"

# 環境変数でオーバーライド可能
CLIENT_SECRET_FILE = Path(os.getenv("SNOWY_GDL_CLIENT_SECRET_FILE", CLIENT_SECRET_FILE))
TOKEN_FILE = Path(os.getenv("SNOWY_GDL_TOKEN_FILE", TOKEN_FILE))

def ensure_directories():
    """必要なディレクトリを自動作成"""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

def get_output_path(filename):
    """出力ファイルのフルパスを取得（実行場所を基準）"""
    return Path.cwd() / filename

def get_config_summary():
    """設定情報をサマリーで返す"""
    return {
        "app_data_dir": str(APP_DATA_DIR),
        "client_secret_file": str(CLIENT_SECRET_FILE),
        "token_file": str(TOKEN_FILE),
        "output_dir": str(Path.cwd()),
    }
