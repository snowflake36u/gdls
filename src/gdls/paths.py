import os
from pathlib import Path
import platform

# アプリケーションに関するパスとファイル名を解決するユーティリティ

APP_GROUP_NAME = 'SnowyTools'
APP_NAME = 'gdls'

def get_app_data_dir() -> Path:
	"""OS別のアプリデータディレクトリを取得
	
	Returns:
		- Windows: %LOCALAPPDATA%\<APP_GROUP_NAME>\GDLS
		- macOS/Linux: ~/.config/<APP_GROUP_NAME>/GDLS
	"""
	system = platform.system()
	if system == 'Windows':
		app_data = Path(os.getenv('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
	else:  # macOS, Linux
		app_data = Path.home() / '.config'
	
	return app_data / APP_GROUP_NAME / APP_NAME

def get_file_path(
		env_var: str,
		default_filename: str,
		cli_arg: str | None = None
) -> Path:
	"""ファイルパスを取得（CLI引数→環境変数→デフォルトの優先順序）
	
	Args:
		env_var: 環境変数名
		default_filename: デフォルトファイル名
		cli_arg: CLI引数で指定されたパス
	
	Returns:
		ファイルのパス
	"""
	if cli_arg:
		return Path(cli_arg)
	env_path = os.getenv(env_var)
	if env_path:
		return Path(env_path)
	return APP_DATA_DIR / default_filename

# デフォルトパス
APP_DATA_DIR = get_app_data_dir()

def get_client_secret_file(cli_arg: str | None = None) -> Path:
	"""client_secret.json のパスを取得
	
	優先順序:
	1. CLI引数が指定されている場合
	2. 環境変数 GDLS_CLIENT_SECRET_FILE
	3. デフォルトパス
	
	Args:
		cli_arg: CLI引数で指定されたパス
	
	Returns:
		client_secret.json のパス
	"""
	return get_file_path(
		'GDLS_CLIENT_SECRET_FILE', 'client_secret.json', cli_arg
	)

def get_token_file(cli_arg: str | None = None) -> Path:
	"""token.json のパスを取得
	
	優先順序:
	1. CLI引数が指定されている場合
	2. 環境変数 GDLS_TOKEN_FILE
	3. デフォルトパス
	
	Args:
		cli_arg: CLI引数で指定されたパス
	
	Returns:
		token.json のパス
	"""
	return get_file_path(
		'GDLS_TOKEN_FILE', 'token.json', cli_arg
	)

# 後方互換性のため、デフォルト値を設定
CLIENT_SECRET_FILE = get_client_secret_file()
TOKEN_FILE = get_token_file()

def ensure_app_data_dir():
	"""必要なディレクトリを自動作成"""
	APP_DATA_DIR.mkdir(parents=True, exist_ok=True)

def get_output_path(filename: str) -> Path:
	"""出力ファイルのフルパスを取得（実行場所を基準）"""
	return Path.cwd() / filename
