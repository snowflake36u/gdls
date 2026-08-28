"""gdls の例外定義。CLI とライブラリ利用の両方で構造化された例外を扱えるようにする。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

class GdlsError(Exception):
	"""アプリケーション固有の基底例外。

	メッセージだけでなく、CLI 表示やライブラリ利用者向けの補助情報を
	例外オブジェクトに保持する。これにより、発生箇所や解決方法を
	例外の呼び出し側が容易に扱える。
	"""
	
	def __init__(
			self,
			message: str,
			*,
			hint: str | None = None,
			details: dict[str, Any] | None = None,
			exit_code: int = 1,
			cause: BaseException | None = None,
	) -> None:
		super().__init__(message)
		self.message = message
		self.hint = hint
		self.details = details or { }
		self.exit_code = exit_code
		self.cause = cause
	
	def __str__(self) -> str:
		return self.message
	
	def format_for_cli(self) -> str:
		"""CLI へ出力するための整形済みメッセージを返す。"""
		lines = [self.message]
		
		if self.details:
			for key, value in self.details.items():
				if value is None:
					continue
				if key in { 'path', 'default_location', 'token_path' }:
					lines.append(f"  - {key}: {value}")
				elif key == 'expected_locations':
					locations = ', '.join(str(item) for item in value)
					lines.append(f"  - expected locations: {locations}")
				else:
					lines.append(f"  - {key}: {value}")
		
		if self.hint:
			lines.append(f"Hint: {self.hint}")
		
		return "\n".join(lines)

class GdlsValueError(GdlsError, ValueError):
	"""ユーザー入力や引数の組み合わせの不正を表す例外。"""
	
	def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None, cause: BaseException | None = None) -> None:
		super().__init__(message, hint=hint, details=details, exit_code=2, cause=cause)

class GdlsFileNotFoundError(GdlsError, FileNotFoundError):
	"""必須ファイルが見つからない場合の例外。"""
	
	def __init__(self, message: str, *, hint: str | None = None, details: dict[str, Any] | None = None, cause: BaseException | None = None) -> None:
		super().__init__(message, hint=hint, details=details, exit_code=1, cause=cause)

class AuthenticationError(GdlsFileNotFoundError):
	"""認証に関する失敗を表す例外。"""

class CredentialFileNotFoundError(AuthenticationError):
	"""OAuth の認証ファイルが見つからない場合の例外。"""
	
	def __init__(
			self,
			missing_path: str | Path | None,
			*,
			default_location: str | Path | None = None,
			env_var: str | None = None,
	) -> None:
		path_text = str(missing_path) if missing_path is not None else 'not specified'
		default_text = str(default_location) if default_location is not None else None
		details: dict[str, Any] = {
			'path': path_text,
			'default_location': default_text,
		}
		if env_var is not None:
			details['env_var'] = env_var
		hint_parts = [
			"Place the file in the 'default_location', or pass the path explicitly with --client-secret.",
		]
		if env_var:
			hint_parts.append(f"The environment variable {env_var} can also be used to set the path.")
		hint = ' '.join(hint_parts)
		super().__init__(
			f"Credential file not found: {path_text}",
			hint=hint,
			details=details,
		)

class ConfigurationError(GdlsValueError):
	"""設定不備や引数の組み合わせ不正を表す例外。"""
