"""gdls のロガー設定とカラー出力用ユーティリティ。"""

import logging
import sys

from .exporter import ANSI_COLOR_RESET

class ColoredFormatter(logging.Formatter):
	"""ログレベルに応じて端末出力の文字色を切り替えるフォーマッタ。

	メッセージ本文を維持したまま、出力先が TTY の場合だけ ANSI エスケープ
	コードを付与する。これにより対話的なターミナルと、リダイレクト先の
	ログの両方を読みやすく保つ。
	"""
	
	LOG_COLORS = {
		logging.DEBUG: '\033[0;36m',
		logging.INFO: '\033[1;32m',
		logging.WARNING: '\033[1;33m',
		logging.ERROR: '\033[1;31m',
		logging.CRITICAL: '\033[1;41m',
	}
	
	def format(self, record: logging.LogRecord) -> str:
		"""TTY であれば ANSI カラー付きの文字列を返す。"""
		message = super().format(record)
		if sys.stderr.isatty():
			color = self.LOG_COLORS.get(record.levelno, '')
			if color:
				return f"{color}{message}{ANSI_COLOR_RESET}"
		return message

def configure_logger(
		logger: logging.Logger | None = None,
		*,
		level: str = 'INFO',
		color: bool = False,
		stream=None,
) -> logging.Logger:
	"""ライブラリ利用と CLI 利用の両方に使えるロガーを設定する。

	Args:
		logger: 設定対象の logger。未指定時はモジュール名 ``gdls`` の logger を作成する。
		level: ``INFO`` や ``DEBUG`` のようなログレベル名。
		color: 端末出力に ANSI カラーを付けるかどうか。
		stream: ``logging.StreamHandler`` に渡す出力先。

	Returns:
		設定済みの logger インスタンス。
	"""
	logger = logger or logging.getLogger('gdls')
	numeric_level = getattr(logging, str(level).upper(), logging.INFO)
	logger.setLevel(numeric_level)
	logger.propagate = False
	
	for handler in list(logger.handlers):
		logger.removeHandler(handler)
		handler.close()
	
	handler = logging.StreamHandler(stream or sys.stderr)
	handler.setFormatter(
		ColoredFormatter('%(message)s') if color else logging.Formatter('%(message)s')
	)
	logger.addHandler(handler)
	return logger
