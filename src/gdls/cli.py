"""gdls のコマンドライン実行入口。"""

import argparse
import logging
import sys

from .exceptions import GdlsError
from .gdls import gdls, parse_fields_arg, validate_arguments
from .logging_utils import configure_logger
from .paths import APP_NAME

__all__ = [
	'parse_arguments',
	'parse_fields_arg',
	'validate_arguments',
	'main',
]

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
	parser.add_argument('-d', '--depth', type=int,
							  help="Limit the depth of recursion when listing subfolders")
	
	# 単一アイテムモード
	item_group = parser.add_mutually_exclusive_group()
	item_group.add_argument('-i', '--item', action='store_true',
									help="Fetch and output information ONLY for the specified target file/folder itself")
	item_group.add_argument('-D', '--describe', action='store_true',
									help="Display detailed information for a single target item in a readable format")
	
	# 出力結果のソート
	parser.add_argument('-s', '--sort', type=str,
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

def main() -> int:
	"""メイン処理を実行する。

	Returns:
		プロセス終了コード。
	"""
	args = parse_arguments()
	
	if args.quiet:
		# quietでは標準出力をデータ専用にするため、非エラーログを抑制する。
		args.log_level = 'ERROR'
	
	logger = configure_logger(
		logger=logging.getLogger(APP_NAME),
		level=args.log_level,
		color=True,
		stream=sys.stderr,
	)
	
	try:
		gdls(
			target=args.target,
			recursive=args.recursive,
			include_trashed=args.include_trashed,
			depth=args.depth,
			item=args.item,
			describe=args.describe,
			sort=args.sort,
			long=args.long,
			fields=args.fields,
			output=args.output,
			output_format=args.output_format,
			append=args.append,
			stdout_format=args.format,
			no_header=args.no_header,
			quiet=args.quiet,
			client_secret=args.client_secret,
			token_file=args.token_file,
			log_level=args.log_level,
			logger=logger,
		)
		return 0
	except GdlsError as exc:
		logger.error(exc.format_for_cli())
		return exc.exit_code
	except ValueError as exc:
		logger.error(f"Error: {exc}")
		return 2
	except FileNotFoundError as exc:
		logger.error(f"File not found: {exc}")
		return 1
	except KeyboardInterrupt:
		logger.error("Operation cancelled by user.")
		return 130
	except Exception:
		logger.error("An unexpected error occurred.")
		logger.error(
			"Detailed exception information:",
			exc_info=True,
		)
		return 1

if __name__ == '__main__':
	main()
