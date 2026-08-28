from pathlib import Path
from platformdirs import user_data_dir

# アプリケーションに関するパスとファイル名を解決するユーティリティ

APP_GROUP_NAME = 'SnowyTools'
APP_NAME = 'gdls'

def resolve_user_data_dir() -> Path:
	"""OS別のアプリデータディレクトリを取得
	
	Returns:
		- Windows: %LOCALAPPDATA%\<APP_GROUP_NAME>\GDLS
	"""
	return Path(user_data_dir(APP_NAME, APP_GROUP_NAME))

# デフォルトパス
USER_DATA_DIR = resolve_user_data_dir()

def resolve_user_data_path(filename: str | Path) -> Path:
	"""APP_DATA_DIR 下のファイルパスを返す（環境変数は参照しない）。
	
	注: CLI 引数や環境変数によるオーバーライドはアプリケーション層で解決し、
	下位モジュールには明示的なパスを渡すことで責務を分離する。
	"""
	return USER_DATA_DIR / filename

def ensure_app_data_dir():
	"""必要なディレクトリを自動作成"""
	USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
