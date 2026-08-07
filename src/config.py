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

# 環境変数またはデフォルトパスから取得する関数
def get_client_secret_file(cli_arg=None):
	"""client_secret.json のパスを取得
	
	優先順序:
	1. CLI引数が指定されている場合
	2. 環境変数 SNOWY_GDL_CLIENT_SECRET_FILE
	3. デフォルトパス
	"""
	if cli_arg:
		return Path(cli_arg)
	env_path = os.getenv("SNOWY_GDL_CLIENT_SECRET_FILE")
	if env_path:
		return Path(env_path)
	return APP_DATA_DIR / "client_secret.json"

def get_token_file(cli_arg=None):
	"""token.json のパスを取得
	
	優先順序:
	1. CLI引数が指定されている場合
	2. 環境変数 SNOWY_GDL_TOKEN_FILE
	3. デフォルトパス
	"""
	if cli_arg:
		return Path(cli_arg)
	env_path = os.getenv("SNOWY_GDL_TOKEN_FILE")
	if env_path:
		return Path(env_path)
	return APP_DATA_DIR / "token.json"

# 後方互換性のため、デフォルト値を設定
CLIENT_SECRET_FILE = get_client_secret_file()
TOKEN_FILE = get_token_file()

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
