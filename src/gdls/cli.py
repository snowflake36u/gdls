import argparse
import logging
import sys

from .auth import get_drive_service
from .exporter import ANSI_COLOR_RESET
from .gdls import extract_drive_id, DEFAULT_LONG_FIELDS, GdlsController
from .repository import DriveRepository

class ColoredFormatter(logging.Formatter):
	"""コンソール出力時にログレベルに応じた着色を行うロギングフォーマッタ。"""
	
	LOG_COLORS = {
		logging.DEBUG: '\033[0;36m',
		logging.INFO: '\033[1;32m',
		logging.WARNING: '\033[1;33m',
		logging.ERROR: '\033[1;31m',
		logging.CRITICAL: '\033[1;41m',
	}
	
	def format(self, record: logging.LogRecord) -> str:
		"""ログメッセージをフォーマットし、端末出力時のみ着色する。

		Args:
			record: ログレコード。

		Returns:
			フォーマット済み文字列。
		"""
		message = super().format(record)
		if sys.stderr.isatty():
			color = self.LOG_COLORS.get(record.levelno, '')
			if color:
				return f"{color}{message}{ANSI_COLOR_RESET}"
		return message

def parse_arguments() -> argparse.Namespace:
	"""コマンドライン引数を解析する。

	Returns:
		パース済みの引数名前空間。
	"""
	parser = argparse.ArgumentParser(
		description="Export Google Drive items to TSV or JSON"
	)
	parser.add_argument('target',
							  help="Google Drive file/folder URL or ID")
	
	# 探索範囲
	parser.add_argument('-R', '--recursive', action='store_true',
							  help="Recursively list items in subfolders")
	parser.add_argument('-t', '--include-trashed', action='store_true',
							  help="Include trashed files in the calculation and output")
	
	# 単一アイテムモード
	item_group = parser.add_mutually_exclusive_group()
	item_group.add_argument('-i', '--item', action='store_true',
									help="Fetch and output information ONLY for the specified target file/folder itself")
	item_group.add_argument('-d', '--describe', action='store_true',
									help="Display detailed information for a single target item in a readable format")
	
	# 出力結果のソート
	parser.add_argument('-S', '--sort', type=str,
							  help="Comma-separated list of keys to sort the output by (e.g., 'size desc, name')")
	
	# 出力属性の指定
	field_group = parser.add_mutually_exclusive_group()
	field_group.add_argument('-l', '--long', action='store_true',
									 help="Output preset basic attributes in long format")
	field_group.add_argument('-f', '--fields',
									 help="Attributes to export (comma separated)")
	
	# 出力ファイル指定
	parser.add_argument('-o', '--output', type=str,
							  help="Output file path")
	parser.add_argument(
		'-O', '--output-format', default=None, choices=['tsv', 'csv', 'json'],
		help="Output file format when writing with --output (default: tsv)",
	)
	parser.add_argument('-a', '--append', action='store_true',
							  help="Append to existing output file")
	
	# 出力形式
	parser.add_argument(
		'-F', '--format', default='auto', choices=['auto', 'table', 'tsv', 'csv', 'json'],
		help="Console output format (default: auto)",
	)
	
	# 出力形式オプション
	parser.add_argument('-H', '--no-header', action='store_true',
							  help="Suppress header line when exporting TSV")
	
	# メッセージ設定
	parser.add_argument('--log-level', type=str, default='INFO',
							  choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
							  help="Set the logging level (default: INFO)")
	parser.add_argument('-q', '--quiet', action='store_true',
							  help="Suppress progress bar and non-error log messages")
	
	# API認証設定
	parser.add_argument('--client-secret', type=str, default=None,
							  help="Path to client_secret.json (overrides GDLS_CLIENT_SECRET_FILE env var)")
	parser.add_argument('--token-file', type=str, default=None,
							  help="Path to token.json (overrides GDLS_TOKEN_FILE env var)")
	
	return parser.parse_args()

def parse_fields_arg(
		fields_arg: str | None,
) -> list[str] | None:
	"""フィールド引数を解析して出力フィールド一覧を取得する。

	Args:
		fields_arg: カンマ区切りのフィールド文字列。

	Returns:
		フィールド一覧、またはNone。
	"""
	if not fields_arg:
		return None
	
	fields_list = [field.strip() for field in fields_arg.split(',') if field.strip()]
	return fields_list if fields_list else None

def validate_arguments(args: argparse.Namespace) -> None:
	"""コマンドライン引数の組み合わせを検証する。

	Args:
		args: パース済みの引数。

	Raises:
		ValueError: サポートされない引数の組み合わせの場合。
	"""
	if args.append and not args.output:
		raise ValueError("The '--append' option requires '--output'.")
	
	if args.no_header and args.format == 'json':
		raise ValueError("The '--no-header' option cannot be used with JSON stdout-format.")
	
	if args.no_header and args.output_format == 'json':
		raise ValueError("The '--no-header' option cannot be used with JSON output-format.")
	
	if args.describe and args.no_header:
		raise ValueError("The '--no-header' option cannot be used with '--describe'.")
	
	if args.output is None and args.output_format is not None:
		raise ValueError("The '--output-format' option is valid only when '--output' is specified.")
	
	if (args.item or args.describe) and args.recursive:
		raise ValueError("The '--item'/'--describe' options are exclusive with '--recursive'.")

def main() -> int:
	"""メイン処理を実行する。

	Returns:
		プロセス終了コード。
	"""
	args = parse_arguments()
	
	if args.quiet:
		# quietでは標準出力をデータ専用にするため、非エラーログを抑制する。
		args.log_level = 'ERROR'
	
	# コマンドライン引数に基づいてロギングを設定し、標準エラー出力に振り向ける
	numeric_level = getattr(logging, args.log_level.upper())
	
	handler = logging.StreamHandler(sys.stderr)
	handler.setFormatter(ColoredFormatter("%(message)s"))
	
	# データ出力との混在を防ぐため、ログは標準エラー出力にルーティングする。
	logging.basicConfig(level=numeric_level, handlers=[handler])
	
	try:
		validate_arguments(args)
	except ValueError as exc:
		logging.error(f"Error: {exc}")
		return 2
	
	target_id = extract_drive_id(args.target)
	if not target_id:
		logging.error("Error: Unable to extract a valid folder ID.")
		return 2
	
	if args.long:
		output_fields = DEFAULT_LONG_FIELDS
	else:
		output_fields = parse_fields_arg(args.fields)
	
	output_format = args.output_format or 'tsv'
	
	try:
		service = get_drive_service(
			client_secret_file=args.client_secret,
			token_file=args.token_file,
		)
		repository = DriveRepository(service)
	except FileNotFoundError as exc:
		logging.error(f"Error: {exc}")
		logging.error(
			"Please place the client secret JSON file at the expected "
			"location or provide its path via --client-secret."
		)
		return 1
	except KeyboardInterrupt:
		logging.error("Operation cancelled by user.")
		return 130
	except Exception:
		logging.error(f"An unexpected error occurred.")
		logging.error(
			"Detailed exception information:",
			exc_info=True,
		)
		return 1
	
	try:
		controller = GdlsController(repository)
		controller.execute(
			target_id=target_id,
			fields=output_fields,
			include_trashed=args.include_trashed,
			item_mode=args.item,
			describe_mode=args.describe,
			recursive_mode=args.recursive,
			no_header=args.no_header,
			output=args.output,
			output_format=output_format,
			stdout_format=args.format,
			append_mode=args.append,
			quiet=args.quiet,
			sort_arg=args.sort,
		)
	except KeyboardInterrupt:
		logging.error("Operation cancelled by user.")
		return 130
	except Exception:
		logging.error(f"Failed to retrieve Google Drive data.")
		logging.error(
			"Detailed exception information:",
			exc_info=True,
		)
		return 1
	
	return 0

if __name__ == '__main__':
	main()
