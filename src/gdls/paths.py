import os
from pathlib import Path
from platformdirs import user_data_dir

# アプリケーションに関するパスとファイル名を解決するユーティリティ

APP_GROUP_NAME = 'SnowyTools'
APP_NAME = 'gdls'

def resolve_runtime_path(
		explicit_path: str | Path | None,
		env_var: str | None,
		default_path: str | Path | None = None,
) -> tuple[str | None, str | None]:
	"""明示指定・環境変数・既定値の優先順でファイルパスを解決する。

	Returns:
		(解決後のパス, 使用されたソース)。source は 'explicit'、'env'、'default'、None のいずれか。
	"""
	if explicit_path is not None:
		return str(explicit_path), 'explicit'
	
	if env_var is not None:
		value = os.getenv(env_var)
		if value is not None:
			return value, 'env'
	
	if default_path is not None:
		return str(default_path), 'default'
	
	return None, None

def resolve_user_data_dir() -> Path:
	"""OS別のアプリデータディレクトリを取得.
	
	Returns:
		- Windows: %LOCALAPPDATA%\<APP_GROUP_NAME>\gdls
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
