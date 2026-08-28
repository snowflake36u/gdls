"""Google Drive からアイテムを取得して出力するための中核ロジック。"""

from __future__ import annotations

import logging
import re
import sys

from pathlib import Path

from progress_reporters import ProgressEvent
from progress_reporters.tqdm_reporter import TqdmProgressReporter
from progress_reporters.trigger import IntervalTrigger

from .logging_utils import configure_logger
from .auth import get_drive_service
from .exceptions import CredentialFileNotFoundError, GdlsValueError
from .paths import APP_NAME, resolve_runtime_path, resolve_user_data_path, ensure_app_data_dir
from .exporter import RecordExporter
from .models import DriveItem
from .repository import DriveRepository
from .scanner import (
	NUMERIC_FIELDS, BOOLEAN_FIELDS, AGGREGATIVE_FIELDS,
	get_required_api_fields, ItemTreeScanner,
)

# 再帰探索の進捗バー更新間隔。
# tqdmの表示更新自体による負荷を抑えつつ、長時間停止して見えることを防ぐ。
SCAN_PROGRESS_UPDATE_TIME_INTERVAL = 0.2

# 再帰探索の進捗バーを件数基準でも更新する間隔。
# 短時間に大量のアイテムが処理された場合でも表示を適度に追従させる。
SCAN_PROGRESS_UPDATE_ITEM_INTERVAL = 1000

# デフォルトの出力フィールド定義
DEFAULT_LONG_FIELDS = [
	'permissions',
	'owners',
	'size',
	'modifiedTime',
	'id',
	'name',
]

# URLやIDの抽出用正規表現（オーバーヘッドを避けるため事前コンパイル）
DRIVE_URL_PATTERN = re.compile(
	r'^https?://(?:[a-zA-Z0-9-]+\.)*google\.com/(?:[^?#]*/)*(?:folders/|file/d/|[^#]*[?&]id=)([a-zA-Z0-9_-]+)(?:[/?&#].*)?$'
)
VALID_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')

def extract_drive_id(url_or_id: str) -> str:
	"""URLまたはID文字列からGoogle DriveアイテムIDのみを抽出する。

	Args:
		url_or_id: Google DriveのURLまたはファイル・フォルダID。
			スラッシュのみ(/)または 'root' が指定された場合は、ルートディレクトリ（マイドライブ）の
			エイリアスとして解決する。

	Returns:
		抽出されたID文字列。
	"""
	if url_or_id == '/' or url_or_id == 'root':
		return 'root'
	
	# Google DriveのURL構造（ドメイン、パス、クエリパラメータ）に厳密に一致するか検証し、
	# ID部分を抽出する。無関係な文字列からの誤抽出を防ぐ。
	match = DRIVE_URL_PATTERN.match(url_or_id)
	if match:
		return match.group(1)
	
	# URLではないが、妥当なID文字列の形式を満たしているか検証する
	if VALID_ID_PATTERN.fullmatch(url_or_id):
		return url_or_id
	
	# 抽出や形式の検証に失敗した場合でも、後続のAPI呼び出しによる
	# エラーハンドリングに委ねるため、元の文字列をそのまま返す。
	return url_or_id

def parse_fields_arg(fields_arg: str | None) -> list[str] | None:
	"""カンマ区切りのフィールド指定を正規化してリストに変換する。

	Args:
		fields_arg: ``"id, name, size"`` のようなフィールド一覧文字列。

	Returns:
		空白除去済みの有効なフィールド名一覧。値が空または無効なら ``None``。
	"""
	if not fields_arg:
		return None
	
	fields_list = [field.strip() for field in fields_arg.split(',') if field.strip()]
	return fields_list if fields_list else None

def validate_arguments(args: object) -> None:
	"""メイン処理の実行前に CLI 引数の組み合わせを検証する。

	この関数は未知の属性を持つオブジェクトでも扱えるよう、あえて寛容に設計
	している。``argparse.Namespace`` だけでなく、テスト用の簡易オブジェクト
	でも利用できる。

	Args:
		args: 実行時に利用する CLI フラグを持つオブジェクト。

	Raises:
		GdlsValueError: 互換しない引数の組み合わせや依存関係の不整合があった場合。
	"""
	if not hasattr(args, 'append'):
		return
	
	if args.append and not getattr(args, 'output', None):
		raise GdlsValueError("The '--append' option requires '--output'.")
	
	if getattr(args, 'no_header', False) and getattr(args, 'format', None) == 'json':
		raise GdlsValueError("The '--no-header' option cannot be used with JSON stdout-format.")
	
	if getattr(args, 'no_header', False) and getattr(args, 'output_format', None) == 'json':
		raise GdlsValueError("The '--no-header' option cannot be used with JSON output-format.")
	
	if getattr(args, 'describe', False) and getattr(args, 'no_header', False):
		raise GdlsValueError("The '--no-header' option cannot be used with '--describe'.")
	
	if getattr(args, 'output', None) is None and getattr(args, 'output_format', None) is not None:
		raise GdlsValueError("The '--output-format' option is valid only when '--output' is specified.")
	
	if (getattr(args, 'item', False) or getattr(args, 'describe', False)) and getattr(args, 'recursive', False):
		raise GdlsValueError("The '--item'/'--describe' options are exclusive with '--recursive'.")
	
	if getattr(args, 'depth', None) and not getattr(args, 'recursive', False):
		raise GdlsValueError("The '--depth' option requires '--recursive'.")

def get_sort_value(key: str, val: object) -> tuple[int, object]:
	"""型の異なる値や文字列形式の非文字列値を比較可能にするためのソートキーを生成する。

	null値（Noneや空文字列）は特別に扱い、データを直接変更することなく
	ソート時に最小の値として優先的に評価する。

	Args:
		key: 属性名。
		val: セル値。

	Returns:
		ソート順序を安定させるためのタプル。
	"""
	if val is None or val == '':
		return 0, ''
	
	if key in NUMERIC_FIELDS:
		if isinstance(val, (int, float)):
			return 1, val
		try:
			return 1, int(val)
		except (TypeError, ValueError):
			try:
				return 1, float(val)
			except (TypeError, ValueError):
				return 0, ''
	
	if key in BOOLEAN_FIELDS:
		if isinstance(val, bool):
			return 1, val
		if isinstance(val, str):
			normalized = val.strip().lower()
			if normalized == 'true':
				return 1, True
			if normalized == 'false':
				return 1, False
		return 1, bool(val)
	
	if isinstance(val, (int, float)):
		return 1, val
	
	return 2, str(val)

def sort_records(records: list[DriveItem], sort_arg: str) -> None:
	"""指定されたキーに基づいてレコードリストをインプレースでソートする。

	安定ソート（リストの元の順序を保持する特性）を利用し、
	優先度の低いキーから順に適用することで複数キーによる並び替えを実現する。

	Args:
		records: ソート対象のレコードリスト。
		sort_arg: カンマ区切りのソートキー（例: 'size desc, name'）。
	"""
	if not sort_arg:
		return
	
	sort_keys = [k.strip() for k in sort_arg.split(',') if k.strip()]
	for key_str in reversed(sort_keys):
		is_desc = key_str.lower().endswith(' desc')
		
		if is_desc:
			actual_key = key_str[:-5].strip()
		elif key_str.lower().endswith(' asc'):
			actual_key = key_str[:-4].strip()
		else:
			actual_key = key_str
		
		records.sort(
			key=lambda r, k=actual_key: get_sort_value(k, r.get(k)),
			reverse=is_desc,
		)

class ScanProgressReporter(TqdmProgressReporter):
	"""ディレクトリツリー探索時の進捗状況を表示するレポータークラス。"""
	
	def __init__(self, quiet: bool) -> None:
		"""ScanProgressReporter を初期化します。

		Args:
			quiet: 進捗バーの表示を抑制するかどうか。
		"""
		super().__init__(
			desc="Processing recursive items",
			position=0,
			disable=quiet,
			file=sys.stderr,
		)
		self.subtask_trigger = IntervalTrigger(
			step_interval=SCAN_PROGRESS_UPDATE_ITEM_INTERVAL,
			time_interval=SCAN_PROGRESS_UPDATE_TIME_INTERVAL,
		)
	
	def on_start(self, event: ProgressEvent) -> None:
		"""初期化時に進捗バーと子孫数カウンタを初期状態へ戻します。"""
		self.subtask_trigger.reset()
		super().on_start(event)
	
	def on_update(self, event: ProgressEvent) -> None:
		"""直下アイテムと子孫アイテムの進捗を表示へ反映します。"""
		with self._lock:
			pbar = self._pbar
			if pbar is None:
				return
			if event.n != 0:
				# 直下アイテムの処理イベント
				pbar.update(event.n)
				pbar.set_postfix_str(
					f"descendants={self.subtask_trigger.steps:,}", refresh=False,
				)
			else:
				# 子孫アイテムの処理イベント
				# 画面のちらつきや描画負荷を防ぐため、一定時間または一定件数ごとのみ進捗を描画する。
				if self.subtask_trigger.step(event.data.get('descendant_increment', 0)):
					pbar.set_postfix_str(
						f"descendants={self.subtask_trigger.steps:,}", refresh=True,
					)

class GdlsController:
	"""Google Driveアイテム取得から出力までの全体フローを統括するコントローラークラス。"""
	
	def __init__(
			self,
			repository: DriveRepository,
			exporter: RecordExporter | None = None,
			scanner: ItemTreeScanner | None = None,
	) -> None:
		"""
		Args:
			repository: Google Drive APIリポジトリ。
			exporter: レコードの出力制御インスタンス。
			scanner: ディレクトリツリーの探索制御インスタンス。
		"""
		self._repository = repository
		self._scanner = scanner or ItemTreeScanner(self._repository)
		self._exporter = exporter or RecordExporter()
	
	def execute(
			self,
			target_id: str,
			fields: list[str] | None = None,
			include_trashed: bool = False,
			item_mode: bool = False,
			describe_mode: bool = False,
			recursive_mode: bool = False,
			max_depth: int | None = None,
			output: str | None = None,
			output_format: str = 'tsv',
			stdout_format: str = 'auto',
			no_header: bool = False,
			append_mode: bool = False,
			quiet: bool = False,
			sort_arg: str | None = None,
			logger: logging.Logger | None = None,
	) -> list[DriveItem]:
		"""指定されたIDのコンテンツを取得し、指定形式で出力する。
		
		Args:
			target_id: 対象のファイルまたはフォルダID。
			fields: 出力対象のフィールド一覧。
			include_trashed: ゴミ箱内のアイテムを含めるかどうか。
			item_mode: 対象アイテム自身のみを出力するかどうか。
			describe_mode: 対象アイテムを詳細表示するかどうか。
			recursive_mode: 子孫要素まで再帰的に取得するかどうか。
			max_depth: 出力するアイテムの探索階層上限。
			output: 出力ファイル名。
			stdout_format: 標準出力の形式。'auto', 'grid', 'table', 'tsv', 'csv', 'json'。
			output_format: 出力ファイルの形式。'tsv', 'csv', 'json'。
			no_header: TSVのヘッダーを抑制するかどうか。
			append_mode: 既存ファイルに追記するかどうか。
			quiet: 進捗バーと非エラーログを抑制するかどうか。
			sort_arg: カンマ区切りのソート指定文字列。
			logger: ログ出力に使う logger。未指定時は独自のロガーを生成する。
		
		Raises:
			Exception: Google Drive APIで取得に失敗した場合。
		"""
		if append_mode and not output:
			raise ValueError("append=True cannot be specified when output is None.")
		
		if no_header and (stdout_format == 'json' or output_format == 'json'):
			raise ValueError(
				"no_header=True cannot be specified when JSON output is enabled."
			)
		
		if no_header and describe_mode:
			raise ValueError("no_header=True cannot be specified when describe_mode=True.")
		
		if fields is not None:
			output_fields = fields
		elif describe_mode:
			output_fields = DEFAULT_LONG_FIELDS
		else:
			output_fields = ['name']
		
		output_path = Path.cwd() / output if output else None
		
		# APIに問い合わせる属性を決定する
		root_api_fields, descendant_api_fields = get_required_api_fields(output_fields)
		needs_descendant_agg = any(
			field in AGGREGATIVE_FIELDS for field in output_fields
		)
		is_single_item_mode = item_mode or describe_mode
		
		# ソートキーが指定されている場合、取得対象のフィールドに含まれる有効なキーか検証する。
		# 取得対象外のフィールドでソートを試みると、データが存在せず意図した結果にならないため。
		if sort_arg and not is_single_item_mode:
			valid_sort_keys = set(output_fields + descendant_api_fields)
			sort_keys = [k.strip() for k in sort_arg.split(',') if k.strip()]
			for key_str in sort_keys:
				actual_key = key_str
				if key_str.lower().endswith(' desc'):
					actual_key = key_str[:-5].strip()
				elif key_str.lower().endswith(' asc'):
					actual_key = key_str[:-4].strip()
				
				if actual_key not in valid_sort_keys:
					raise ValueError(f"Invalid sort key: '{actual_key}'. Valid keys are: {', '.join(sorted(valid_sort_keys))}")
		
		logger = logger or logging.getLogger(APP_NAME)
		logger.info("Fetching data from Google Drive...")
		
		records_to_output = self._fetch_records(
			target_id=target_id,
			output_fields=output_fields,
			root_api_fields=root_api_fields,
			descendant_api_fields=descendant_api_fields,
			include_trashed=include_trashed,
			is_single_item_mode=is_single_item_mode,
			recursive_mode=recursive_mode,
			needs_descendant_agg=needs_descendant_agg,
			quiet=quiet,
			max_depth=max_depth,
		)
		
		if sort_arg and not is_single_item_mode:
			sort_records(records_to_output, sort_arg)
		
		self._export_records(
			records_to_output=records_to_output,
			output_fields=output_fields,
			describe_mode=describe_mode,
			stdout_format=stdout_format,
			output_format=output_format,
			output_path=output_path,
			append_mode=append_mode,
			no_header=no_header,
		)
		
		logger.info(f"Items: {len(records_to_output)}")
		if output_path:
			logger.info(f"Output to: {output_path}")
		return records_to_output
	
	def _fetch_records(
			self,
			target_id: str,
			output_fields: list[str],
			root_api_fields: list[str],
			descendant_api_fields: list[str],
			include_trashed: bool,
			is_single_item_mode: bool,
			recursive_mode: bool,
			needs_descendant_agg: bool,
			quiet: bool,
			max_depth: int | None = None,
	) -> list[DriveItem]:
		"""データを取得・構築し、出力対象のレコードリストを生成する。

		Args:
			target_id: 対象のファイルまたはフォルダID。
			output_fields: 出力対象のフィールド一覧。
			root_api_fields: ルートアイテムに要求するAPIフィールド一覧。
			descendant_api_fields: 子孫アイテムに要求するAPIフィールド一覧。
			include_trashed: ゴミ箱のアイテムを含めるかどうか。
			is_single_item_mode: 単一アイテム処理モードかどうか。
			recursive_mode: 再帰探索モードかどうか。
			max_depth: 出力するアイテムの探索階層上限。
			needs_descendant_agg: 子孫集約が必要かどうか。
			quiet: ログ・プログレスバーを抑制するかどうか。

		Returns:
			出力用に構築されたレコードのリスト。
		"""
		if is_single_item_mode:
			# 対象アイテムそのものを1つだけ取得する。
			item = self._repository.fetch_item(target_id, root_api_fields)
			
			target_record = self._scanner.build_record(
				item, output_fields,
				relative_path=item.get('name', ''), depth=0,
			)
			records_to_output = [target_record]
			
			if needs_descendant_agg and target_record.is_folder():
				# 単一アイテムの集約値を計算するため内部的に子孫を走査するが、出力はルート（対象自身）のみ。
				_, root_records = self._scanner.scan(
					item['id'],
					descendant_api_fields,
					output_fields,
					include_trashed,
				)
				
				# 直下アイテムの集約結果を対象アイテム自身のレコードへ合算する
				for child_record in root_records:
					ItemTreeScanner.propagate_to_parent(child_record, target_record, output_fields)
		
		elif recursive_mode or needs_descendant_agg:
			# 再帰探索、集約、レコード構築を同じ走査で実行する。
			# recursive_modeがFalseの場合でも、集約値計算のために子孫を取得するが、
			# その場合の最終的な出力対象は直下アイテム（root_records）のみに絞り込む。
			reporter = ScanProgressReporter(quiet=quiet)
			all_records, root_records = self._scanner.scan(
				target_id,
				root_api_fields,
				output_fields,
				include_trashed,
				needs_descendant_agg=needs_descendant_agg,
				reporter=reporter,
				max_depth=max_depth if recursive_mode else None,
			)
			
			records_to_output = all_records if recursive_mode else root_records
		
		else:
			# 集約が不要な場合は、指定フォルダの直下要素だけを取得する。
			root_items = self._repository.fetch_children(
				target_id, root_api_fields, include_trashed,
			)
			
			records_to_output = [
				self._scanner.build_record(
					child, output_fields,
					relative_path=child.get('name', ''), depth=1,
				)
				for child in root_items
			]
		
		# 直下要素が0件だった場合、対象ID自体が非フォルダである可能性がある。
		if not is_single_item_mode and not records_to_output:
			# 対象が非フォルダであった場合に自身のレコードを構築するため、必要なフィールドを網羅して取得する
			all_required_fields = list(set(root_api_fields + descendant_api_fields))
			target_info = self._repository.fetch_item(target_id, all_required_fields)
			
			if not target_info.is_folder():
				# フォルダでない場合は、Linuxのlsコマンドの慣習に合わせて対象アイテム自身を出力結果とする
				records_to_output = [
					self._scanner.build_record(
						target_info, output_fields,
						relative_path=target_info.get('name', ''), depth=0,
					)
				]
		
		return records_to_output
	
	def _export_records(
			self,
			records_to_output: list[DriveItem],
			output_fields: list[str],
			describe_mode: bool,
			stdout_format: str,
			output_format: str,
			output_path: Path | None,
			append_mode: bool,
			no_header: bool,
	) -> None:
		"""取得したレコードを指定された形式で出力する。

		Args:
			records_to_output: 出力対象のレコードリスト。
			output_fields: 出力対象のフィールド一覧。
			describe_mode: 詳細表示モードかどうか。
			output_format: 出力フォーマット名 ('tsv' または 'json')。
			output_path: 出力ファイルパス。
			append_mode: ファイル追記モードかどうか。
			no_header: TSV出力においてヘッダーを抑制するかどうか。
		"""
		if describe_mode:
			if stdout_format not in ('auto', 'json'):
				raise ValueError(
					"stdout_format must be 'auto' or 'json' in describe mode."
				)
			if output_format not in ('tsv', 'json'):
				raise ValueError(
					"output_format must be 'tsv' or 'json' in describe mode."
				)
			
			if not records_to_output:
				return
			
			if output_format == 'json' or stdout_format == 'json':
				# describe_mode の既存JSON表現は、単一レコードをオブジェクトとしてstdoutへ、
				# ファイル追記時のみ配列として保持する仕様を維持する。
				if stdout_format == 'json':
					self._exporter.export_describe(
						records_to_output[0],
						None,
						use_json=True,
						append=False,
					)
				else:
					self._exporter.export_describe(
						records_to_output[0],
						None,
						use_json=False,
						append=False,
					)
				if output_path:
					self._exporter.export_describe(
						records_to_output[0],
						output_path,
						use_json=(output_format == 'json'),
						append=append_mode,
					)
				return
			
			# 人間向けdescribeをstdoutとファイルへ出力。
			self._exporter.export_describe(
				records_to_output[0],
				output_path,
				use_json=False,
				append=append_mode,
			)
			return
		
		# 非 describe_mode での標準出力・ファイル出力を実行する
		self._exporter.export(
			records=records_to_output,
			fields=output_fields,
			stdout_format=stdout_format,
			output_path=output_path,
			output_format=output_format,
			append=append_mode,
			no_header=no_header,
		)

def gdls(
		target: str,
		*,
		recursive: bool = False,
		include_trashed: bool = False,
		depth: int | None = None,
		item: bool = False,
		describe: bool = False,
		sort: str | None = None,
		long: bool = False,
		fields: str | list[str] | None = None,
		output: str | None = None,
		output_format: str | None = None,
		append: bool = False,
		stdout_format: str = 'auto',
		no_header: bool = False,
		quiet: bool = False,
		client_secret: str | None = None,
		token_file: str | None = None,
		log_level: str = 'INFO',
		logger: logging.Logger | None = None,
		color: bool = False,
		stream=None,
) -> list[DriveItem]:
	"""Google Drive から対象アイテムを取得し、必要に応じて再帰探索・ソート・出力までをまとめて実行する。

	この関数はライブラリ利用者向けの公開 API であり、CLI からの呼び出しも
	内部的にはこの関数を経由する。対象の Google Drive URL あるいは ID を
	受け取り、認証済みの Drive API クライアントを作成してリポジトリと
	コントローラを組み立てたうえで、指定されたフィールドや書式でレコードを
	取得・整形・出力する。

	Args:
		target: Google Drive のファイルまたはフォルダの URL、またはアイテム ID。
		recursive: 対象フォルダ配下を再帰的に探索して子孫アイテムも取得するかどうか。
		include_trashed: ゴミ箱内のアイテムも結果に含めるかどうか。
		depth: recursive=True 時に探索の最大深さを制限する。None なら制限なし。
		item: 対象そのものだけを取得して出力するかどうか。
		describe: 対象アイテムを人間向けの詳細表示形式で出力するかどうか。
		sort: カンマ区切りのソート指定。例: "size desc, name"。
		long: 事前定義された基本フィールド一覧を使用するかどうか。
		fields: 出力対象フィールドの文字列または一覧。long=True の場合は優先される。
		output: 出力先ファイルパス。指定時は結果をファイルへも書き出す。
		output_format: 出力ファイルの形式。'tsv' / 'csv' / 'json' のいずれか。
		append: 既存の出力ファイルへ追記するかどうか。
		stdout_format: 標準出力の形式。'auto' / 'table' / 'tsv' / 'csv' / 'json'。
		no_header: TSV 形式の出力時にヘッダー行を抑制するかどうか。
		quiet: 進捗バーや通常ログを抑制し、エラーのみを表示するかどうか。
		client_secret: OAuth 用の client_secret.json のパス。未指定時は環境変数や既定ロケーションを利用する。
		token_file: 認証トークンの保存先 JSON パス。未指定時は既定ファイルを利用する。
		log_level: ログレベル。'DEBUG' / 'INFO' / 'WARNING' / 'ERROR' / 'CRITICAL'。
		logger: ログ出力に使う logger。未指定時は内部で生成する。
		color: logger 未指定時にカラー付きログを使うかどうか。
		stream: logger 未指定時に使用する出力ストリーム。

	Returns:
		取得・整形された ``DriveItem`` のリスト。出力先にファイルを書き出した場合でも、
		関数の戻り値としてレコード一覧を返す。

	Raises:
		ValueError: 引数の組み合わせが不正、または対象 ID の抽出に失敗した場合。
		FileNotFoundError: 認証用 JSON ファイルが見つからない場合。
		Exception: Google Drive API やファイル出力処理で予期しない失敗が発生した場合。
	"""
	if quiet:
		log_level = 'ERROR'
	
	if logger is None:
		logger = configure_logger(
			level=log_level,
			color=color,
			stream=stream,
		)
	else:
		logger.setLevel(getattr(logging, str(log_level).upper(), logging.INFO))
	
	args = type('Args', (), {
		'target': target,
		'recursive': recursive,
		'include_trashed': include_trashed,
		'depth': depth,
		'item': item,
		'describe': describe,
		'sort': sort,
		'long': long,
		'fields': fields,
		'output': output,
		'output_format': output_format,
		'append': append,
		'format': stdout_format,
		'no_header': no_header,
		'quiet': quiet,
		'client_secret': client_secret,
		'token_file': token_file,
		'log_level': log_level,
	})()
	
	validate_arguments(args)
	ensure_app_data_dir()
	
	target_id = extract_drive_id(target)
	if not target_id:
		raise GdlsValueError(
			"Unable to extract a valid folder ID.",
			hint="Use a Google Drive folder/file URL, an item ID, '/', or 'root'.",
		)
	
	if args.long:
		output_fields = DEFAULT_LONG_FIELDS
	else:
		output_fields = parse_fields_arg(args.fields) if isinstance(args.fields, str) else list(args.fields) if args.fields else None
	
	output_format = args.output_format or 'tsv'
	
	# 認証ファイルパスの特定
	default_client_secret = resolve_user_data_path('client_secret.json')
	default_token_file = resolve_user_data_path('token.json')
	
	resolved_client_secret, secret_source = resolve_runtime_path(
		args.client_secret,
		'GDLS_CLIENT_SECRET_FILE',
		default_client_secret,
	)
	resolved_token_file, token_source = resolve_runtime_path(
		args.token_file,
		'GDLS_TOKEN_FILE',
		default_token_file,
	)
	
	# 確定したパスを渡して認証を実行
	try:
		service = get_drive_service(
			client_secret_file=resolved_client_secret,
			token_file=resolved_token_file,
		)
	except FileNotFoundError as exc:
		# ユーザーが client_secret を明示指定したわけではない場合、
		# 環境変数による設定が可能であることをヒントとして含める
		env_var = 'GDLS_CLIENT_SECRET_FILE' if secret_source != 'explicit' else None
		
		# auth.py から伝播してきた FileNotFoundError を、詳細な文脈を持った専用例外に包み直す
		raise CredentialFileNotFoundError(
			resolved_client_secret,
			default_location=default_client_secret,
			env_var=env_var,
		) from exc
	
	repository = DriveRepository(service)
	controller = GdlsController(repository)
	return controller.execute(
		target_id=target_id,
		fields=output_fields,
		include_trashed=args.include_trashed,
		item_mode=args.item,
		describe_mode=args.describe,
		recursive_mode=args.recursive,
		max_depth=args.depth,
		output=args.output,
		output_format=output_format,
		stdout_format=args.format,
		no_header=args.no_header,
		append_mode=args.append,
		quiet=args.quiet,
		sort_arg=args.sort,
		logger=logger,
	)
