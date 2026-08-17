import argparse
import csv
import json
import logging
import re
import shutil
import sys
import time
import unicodedata
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from tqdm import tqdm

from config import (
	APP_NAME,
	ensure_directories,
	get_client_secret_file,
	get_output_path,
	get_token_file,
)

# スコープの設定（読み取り専用）
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Google DriveフォルダのMIMEタイプ
FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'

# コンソール出力用のANSIエスケープコード
ANSI_COLOR_BLUE = '\033[1;34m'
ANSI_COLOR_CYAN = '\033[1;36m'
ANSI_COLOR_HEADER = '\033[1;33m'
ANSI_COLOR_RESET = '\033[0m'

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

# デフォルトの出力フィールド定義
DEFAULT_LONG_FIELDS = [
	'permissions',
	'owners',
	'size',
	'modifiedTime',
	'id',
	'name',
]

# 出力フィールドと必要なAPIフィールドの対応マッピング
FIELD_API_DEPENDENCIES: dict[str, list[str]] = {
	'oldestCreatedTime': ['createdTime'],
	'totalSize': ['size'],
	'totalQuotaBytesUsed': ['quotaBytesUsed'],
	'relativePath': ['name'],
	'depth': [],
	'permissions': ['mimeType', 'capabilities/canEdit', 'shared'],
}

# 子孫要素の取得・集約が必要な属性一覧
AGGREGATIVE_FIELDS: set[str] = {
	'oldestCreatedTime',  # 自分自身および子孫アイテムのうち最古の作成日時
	'totalSize',
	'totalQuotaBytesUsed',
}

# 数値として評価・ソートすべき非文字列属性および数値属性
NUMERIC_FIELDS: set[str] = {
	'size',
	'quotaBytesUsed',
	'version',
	'depth',
}

# ブール値として評価・ソートすべき属性
BOOLEAN_FIELDS: set[str] = {
	'starred',
	'trashed',
	'explicitlyTrashed',
	'isAppAuthorized',
	'shared',
	'ownedByMe',
	'viewedByMe',
	'modifiedByMe',
	'hasThumbnail',
	'writersCanShare',
	'copyRequiresWriterPermission',
	'hasAugmentedPermissions',
}

# 再帰探索の進捗バー更新間隔。
# tqdmの表示更新自体による負荷を抑えつつ、長時間停止して見えることを防ぐ。
RECURSIVE_PROGRESS_UPDATE_INTERVAL = 0.2

# 再帰探索の進捗バーを件数基準でも更新する間隔。
# 短時間に大量のアイテムが処理された場合でも表示を適度に追従させる。
RECURSIVE_PROGRESS_UPDATE_ITEMS = 1000

def get_drive_service(
		client_secret_file: str | None = None,
		token_file: str | None = None,
) -> Resource:
	"""Google Drive APIのサービスを構築・認証する。
	
	Args:
		client_secret_file: クライアントシークレットファイルのパス。
		token_file: トークンファイルのパス。
	
	Returns:
		認証済みGoogle Drive APIサービス。
	
	Raises:
		FileNotFoundError: クライアントシークレットファイルが存在しない場合。
	"""
	# パスを解決（CLIで指定されたか、環境変数か、デフォルト）
	secret_path = get_client_secret_file(client_secret_file)
	token_path = get_token_file(token_file)
	
	ensure_directories()
	creds = None
	
	if token_path.exists():
		creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
	
	if not creds or not creds.valid:
		if creds and creds.expired and creds.refresh_token:
			creds.refresh(Request())
		else:
			if not secret_path.exists():
				raise FileNotFoundError(f"Client secret file not found at: {secret_path}")
			flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
			# ローカルサーバーを起動して認証
			creds = flow.run_local_server(port=0)
		with open(token_path, 'w', encoding='utf-8') as token:
			token.write(creds.to_json())
	return build('drive', 'v3', credentials=creds)

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
	match = re.match(
		r'^https?://(?:[a-zA-Z0-9-]+\.)*google\.com/(?:[^?#]*/)*(?:folders/|file/d/|[^#]*[?&]id=)([a-zA-Z0-9_-]+)(?:[/?&#].*)?$',
		url_or_id
	)
	if match:
		return match.group(1)
	
	# URLではないが、妥当なID文字列の形式を満たしているか検証する
	if re.fullmatch(r'[a-zA-Z0-9_-]+', url_or_id):
		return url_or_id
	
	# 抽出や形式の検証に失敗した場合でも、後続のAPI呼び出しによる
	# エラーハンドリングに委ねるため、元の文字列をそのまま返す。
	return url_or_id

def get_required_api_fields(
		output_fields: list[str],
) -> tuple[list[str], list[str]]:
	"""出力対象フィールドに必要なAPIフィールドを決定する。

	Args:
		output_fields: 出力対象フィールド。

	Returns:
		ルートアイテム用フィールドと子孫アイテム用フィールドのタプル。
	"""
	root_field_set = { 'id', 'mimeType', 'name' }
	descendant_field_set = set(root_field_set)
	
	for field in output_fields:
		# APIフィールド依存が定義されていればそれを採用し、
		# なければ属性名をそのままAPIフィールド名として扱う。
		dependencies = FIELD_API_DEPENDENCIES.get(field, [field])
		
		root_field_set.update(dependencies)
		
		if field in AGGREGATIVE_FIELDS:
			descendant_field_set.update(dependencies)
	
	return list(root_field_set), list(descendant_field_set)

def fetch_children(
		service: Resource,
		folder_id: str,
		api_fields: list[str],
		include_trashed: bool = False,
) -> list[dict]:
	"""指定フォルダの直下の要素一覧を取得する。

	Args:
		service: Google Drive APIサービス。
		folder_id: 対象フォルダID。
		api_fields: APIから取得するフィールド。
		include_trashed: ゴミ箱内の要素を含めるかどうか。

	Returns:
		直下のアイテム一覧。
	"""
	trashed_query = '' if include_trashed else ' and trashed=false'
	query = f"'{folder_id}' in parents{trashed_query}"
	fields = f"nextPageToken, files({', '.join(api_fields)})"
	
	children = []
	page_token = None
	
	while True:
		response = service.files().list(
			q=query,
			spaces='drive',
			fields=fields,
			pageToken=page_token,
			includeItemsFromAllDrives=True,
			supportsAllDrives=True,
		).execute()
		
		children.extend(response.get('files', []))
		
		page_token = response.get('nextPageToken')
		if not page_token:
			break
	
	return children

def propagate_to_parent(
		item_record: dict,
		parent_record: dict,
		output_fields: list[str],
) -> None:
	"""子要素の集約値を親要素のレコードへ加算・伝播する。

	Args:
		item_record: 子要素のレコード。
		parent_record: 親要素のレコード。
		output_fields: 出力対象のフィールド一覧。
	"""
	if 'oldestCreatedTime' in output_fields:
		item_oldest = item_record['oldestCreatedTime']
		parent_oldest = parent_record['oldestCreatedTime']
		
		# 最古作成日時の更新（文字列の辞書順比較によって判別）
		if item_oldest and (
				not parent_oldest or item_oldest < parent_oldest
		):
			parent_record['oldestCreatedTime'] = item_oldest
	
	if 'totalSize' in output_fields:
		parent_record['totalSize'] += item_record['totalSize']
	
	if 'totalQuotaBytesUsed' in output_fields:
		parent_record['totalQuotaBytesUsed'] += item_record['totalQuotaBytesUsed']

def fetch_records_recursively(
		service: Resource,
		root_folder_id: str,
		api_fields: list[str],
		output_fields: list[str],
		include_trashed: bool = False,
		quiet: bool = False,
		needs_descendant_agg: bool = True,
) -> tuple[list[dict], list[dict]]:
	"""指定フォルダ配下のすべての要素を反復処理で全件取得し、
	データの集約とレコード構築を行う。

	関数呼び出しのオーバーヘッド削減と、深くネストされたディレクトリ構造での
	再帰呼び出し上限（RecursionError）を回避するため、内部的にスタック構造を
	用いた深さ優先探索（DFS）アルゴリズムを採用している。

	Args:
		service: Google Drive APIサービス。
		root_folder_id: 起点フォルダID。
		api_fields: APIから取得するフィールド。
		output_fields: 出力対象のフィールド。
		include_trashed: ゴミ箱内の要素を含めるかどうか。
		quiet: 進捗表示を抑制するかどうか。
		needs_descendant_agg: 子孫集約値を計算するかどうか。

	Returns:
		構築済みレコードのリストと、直下アイテムのレコードのリストのタプル。
	"""
	root_children = fetch_children(
		service, root_folder_id, api_fields, include_trashed,
	)
	
	records = []
	root_records = []
	
	# 集計処理のために、IDベースでレコードや親子関係を参照する辞書
	records_by_id = { }
	parent_ids: dict[str, str | None] = { }
	
	with tqdm(
			root_children,
			desc="Processing recursive items",
			unit=" item",
			position=0,
			disable=quiet,
			file=sys.stderr,
	) as progress:
		for child in progress:
			descendant_count = 0
			last_progress_update = time.monotonic()
			next_progress_update_count = RECURSIVE_PROGRESS_UPDATE_ITEMS
			
			# 直下アイテムのレコードを作成する
			child_name = child.get('name', '')
			child_id = child['id']
			child_depth = 1
			
			record = create_item_record(
				child, output_fields,
				relative_path=child_name, depth=child_depth,
			)
			records.append(record)
			records_by_id[child_id] = record
			root_records.append(record)
			parent_ids[child_id] = root_folder_id
			
			if not is_folder_item(child):
				progress.set_postfix_str(
					"descendants=0", refresh=False,
				)
				continue
			
			# === フォルダの場合は子孫要素を再帰的に探索する ===
			
			# 反復的な深さ優先探索のためのスタック構造。
			# 要素のタプル: (要素ID, 親パス, 階層の深さ, 帰りがけフラグ)
			# 帰りがけフラグ(finalize)がTrueの場合、子孫の探索が完了した後の集約処理を行う。
			stack = [(child_id, child_name, child_depth, False)]
			
			while stack:
				current_id, parent_path, depth, finalize = stack.pop()
				
				if finalize:
					# =====
					# 帰りがけの集計伝播処理（子孫の走査が完了した後に実行される）
					# 自身のレコードを補完し、親に情報を伝播する。
					# =====
					parent_id = parent_ids[current_id]
					if parent_id and parent_id in records_by_id:
						propagate_to_parent(
							records_by_id[current_id],
							records_by_id[parent_id],
							output_fields,
						)
					continue
				
				# =====
				# 行きがけの処理
				# 対象フォルダ内のアイテムを取得し、スタックに追加する。
				# =====
				descendants = fetch_children(
					service, current_id, api_fields, include_trashed,
				)
				
				if needs_descendant_agg:
					# 自身についての帰りがけ処理（finalize=True）をスタックに積む。
					# LIFO(後入れ先出し)のため、これから積まれる子ノードの処理が全て終わった後に実行される。
					stack.append((current_id, parent_path, depth, True))
				
				# 子ノードをスタックに積む。
				# reversed() を使用することで、APIから取得した順序（元の配列順）でスタックから取り出せるようにする。
				desc_depth = depth + 1
				for desc in reversed(descendants):
					desc_name = desc.get('name', '')
					relative_path = f'{parent_path}/{desc_name}' if parent_path else desc_name
					
					record = create_item_record(
						desc, output_fields,
						relative_path=relative_path, depth=desc_depth,
					)
					records.append(record)
					records_by_id[desc['id']] = record
					parent_ids[desc['id']] = current_id
					
					if is_folder_item(desc):
						# 子アイテムがフォルダの場合は、さらにその内部を走査するためスタックに積む（行きがけ処理）
						stack.append((desc['id'], relative_path, desc_depth, False))
					elif needs_descendant_agg:
						# 子アイテムがファイルで、集約が必要な場合は、自身の情報を親へ伝播させるため帰りがけ処理のみ積む
						stack.append((desc['id'], relative_path, desc_depth, True))
					
					# === 進捗更新 ===
					descendant_count += 1
					current_time = time.monotonic()
					# 画面のちらつきや描画負荷を防ぐため、一定時間または一定件数ごとのみ進捗を描画する。
					if descendant_count >= next_progress_update_count \
							or current_time - last_progress_update >= RECURSIVE_PROGRESS_UPDATE_INTERVAL:
						progress.set_postfix_str(
							f"descendants={descendant_count:,}", refresh=False,
						)
						last_progress_update = current_time
						
						while descendant_count >= next_progress_update_count:
							next_progress_update_count += RECURSIVE_PROGRESS_UPDATE_ITEMS
			
			# 現在のトップレベル子の探索完了時には必ず最新値を表示する。
			progress.set_postfix_str(
				f'descendants={descendant_count:,}', refresh=False,
			)
	
	return records, root_records

def get_owner_name(item: dict) -> str:
	"""アイテムからオーナー名を取得する。

	Args:
		item: Google Driveアイテム。

	Returns:
		オーナーの表示名。取得できない場合は空文字列。
	"""
	owners = item.get('owners', [])
	
	if not owners:
		return ''
	
	return owners[0].get('displayName', '')

def is_folder_item(item: dict) -> bool:
	"""アイテムがフォルダかどうかを判定する。

	Args:
		item: Google Driveアイテム。

	Returns:
		フォルダの場合はTrue。
	"""
	return item.get('mimeType') == FOLDER_MIME_TYPE

def get_permissions_string(item: dict) -> str:
	"""アイテムからlsコマンドのようなパーミッション文字列を生成する。

	ファイルタイプ(1文字)、読み書き実行の権限(3文字)、共有ステータス(1文字)を
	組み合わせた5文字の文字列を構築する。

	Args:
		item: Google Driveアイテム。

	Returns:
		フォーマットされたパーミッション文字列。
	"""
	mime_type = item.get('mimeType', '')
	
	if mime_type == FOLDER_MIME_TYPE:
		type_char = 'd'
	elif mime_type == 'application/vnd.google-apps.shortcut':
		type_char = 'l'
	else:
		type_char = '-'
	
	capabilities = item.get('capabilities', { })
	# API経由でアイテム情報を取得できている時点で、読み取り権限(Read)は必ず存在する
	can_read = True
	can_edit = capabilities.get('canEdit', False)
	
	read_char = 'r' if can_read else '-'
	write_char = 'w' if can_edit else '-'
	# フォルダとして認識されるものはディレクトリ移動可能とみなして 'x' を付与する
	exec_char = 'x' if type_char == 'd' else '-'
	
	shared_char = '+' if item.get('shared', False) else '-'
	
	return f'{type_char}{read_char}{write_char}{exec_char}{shared_char}'

def create_item_record(
		item: dict,
		fields: list[str],
		relative_path: str | None = None,
		depth: int = 0,
) -> dict:
	"""Google Driveアイテムのレコードを構築する。

	指定された出力フィールドに基づき、APIレスポンスから必要な属性を抽出・整形する。

	Args:
		item: APIから取得したアイテム。
		fields: 出力対象のフィールド一覧。
		relative_path: アイテムのルートからの相対パス。
		depth: アイテムの階層深度。

	Returns:
		構築されたレコード辞書。各属性の値には未集約の初期値が設定される。
	"""
	owner_name = get_owner_name(item)
	
	# レコードを構築
	record = { }
	for field in fields:
		if field == 'owners':
			record[field] = owner_name
		elif field == 'permissions':
			record[field] = get_permissions_string(item)
		elif field == 'oldestCreatedTime':
			# 初期値は自身の作成日時とし、探索完了後に子孫を含む集約値へ更新する。
			record[field] = item.get('createdTime', '')
		elif field == 'totalSize':
			# 初期値は自身のサイズとし、探索完了後に子孫を含む集約値へ更新する。
			record[field] = int(item.get('size') or 0)
		elif field == 'totalQuotaBytesUsed':
			# 初期値は自身の使用量とし、探索完了後に子孫を含む集約値へ更新する。
			record[field] = int(item.get('quotaBytesUsed') or 0)
		elif field == 'relativePath':
			record[field] = item.get('name', '') if relative_path is None else relative_path
		elif field == 'depth':
			record[field] = depth
		elif field == 'parents':
			# API仕様上、親IDのリストとして返却されるため直接カンマ区切りで結合する。
			record[field] = ','.join(item.get('parents', []))
		else:
			# APIが返した値をそのまま設定する
			record[field] = item.get(field, '')
	
	# コンソール出力時の色付け判定等に使用する内部用メタデータ
	record['_mimeType'] = item.get('mimeType', '')
	
	return record

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
		return (0, '')
	
	if key in NUMERIC_FIELDS:
		if isinstance(val, (int, float)):
			return (1, val)
		try:
			return (1, int(val))
		except ValueError:
			try:
				return (1, float(val))
			except ValueError:
				return (0, '')
	
	if key in BOOLEAN_FIELDS:
		if isinstance(val, bool):
			return (1, val)
		if isinstance(val, str):
			normalized = val.strip().lower()
			if normalized == 'true':
				return (1, True)
			if normalized == 'false':
				return (1, False)
		return (1, bool(val))
	
	if isinstance(val, (int, float)):
		return (1, val)
	
	return (2, str(val))

def sort_records(records: list[dict], sort_arg: str) -> None:
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

def format_cell_for_tsv(val: object) -> str:
	"""TSVの各セル値を出力用文字列に変換する。

	Args:
		val: セル値。

	Returns:
		TSVとして出力可能な文字列。
	"""
	if val is None:
		return ''
	if isinstance(val, (dict, list)):
		return json.dumps(val, ensure_ascii=False)
	return str(val)

def get_display_width(text: str) -> int:
	"""文字列の表示幅を計算する。

	コンソール等で等幅フォントを使用した場合の視覚的な文字幅を算出する。
	全角文字は2、半角文字は1として計算する。
	"""
	width = 0
	for char in text:
		if unicodedata.east_asian_width(char) in ('F', 'W', 'A'):
			width += 2
		else:
			width += 1
	return width

def format_grid_item(record: dict) -> tuple[str, int]:
	"""グリッド出力用にアイテム名と表示幅を取得し、端末出力時は色付けを行う。

	Args:
		record: Google Driveアイテムのレコード。

	Returns:
		フォーマット済み表示名と計算された表示幅のタプル。
	"""
	name = str(record.get('name', ''))
	width = get_display_width(name)
	
	if not sys.stdout.isatty():
		return name, width
	
	mime_type = record.get('_mimeType', '')
	if mime_type == FOLDER_MIME_TYPE:
		formatted = f"{ANSI_COLOR_BLUE}{name}{ANSI_COLOR_RESET}"
	elif mime_type == 'application/vnd.google-apps.shortcut':
		formatted = f"{ANSI_COLOR_CYAN}{name}{ANSI_COLOR_RESET}"
	else:
		formatted = name
	
	return formatted, width

def write_records_as_grid(records: list[dict]) -> None:
	"""端末の表示幅に合わせて要素を列優先のグリッド形式で標準出力へ表示する。

	Args:
		records: 出力対象のレコード一覧。
	"""
	if not records:
		return
	
	formatted_items = [format_grid_item(record) for record in records]
	max_name_width = max(width for _, width in formatted_items)
	column_spacing = 2
	
	terminal_width = shutil.get_terminal_size().columns
	total_item_width = max_name_width + column_spacing
	num_columns = max(1, terminal_width // total_item_width)
	
	total_items = len(formatted_items)
	num_rows = (total_items + num_columns - 1) // num_columns
	
	for row in range(num_rows):
		line_parts = []
		for col in range(num_columns):
			index = row + col * num_rows
			if index < total_items:
				formatted_name, width = formatted_items[index]
				# 最終列および最終要素以外には列間隔用のパディングを付与する
				if col < num_columns - 1 and index + num_rows < total_items:
					padding = ' ' * (max_name_width - width + column_spacing)
					line_parts.append(formatted_name + padding)
				else:
					line_parts.append(formatted_name)
		sys.stdout.write(''.join(line_parts) + '\n')

def write_records_to_tsv(
		records: list[dict],
		fields: list[str],
		output: Path | None,
		append: bool = False,
		no_header: bool = False,
) -> None:
	"""レコードを標準出力と指定ファイルにTSV形式またはグリッド形式で出力する。

	Args:
		records: 書き込むレコード一覧。
		fields: 出力対象のフィールド一覧。
		output: 出力ファイルパス。Noneの場合は標準出力のみ。
		append: 既存ファイルへの追記を行うかどうか。
		no_header: ヘッダー行を出力しないかどうか。
	"""
	# 複雑なオブジェクト型の値を文字列化してフォーマット崩れを防ぐ
	formatted_records = [
		{ k: format_cell_for_tsv(v) for k, v in record.items() }
		for record in records
	]
	
	# 実行環境がコンソールの場合は視認性を考慮したフォーマットを行い、
	# パイプやリダイレクトの場合は機械処理に適したTSVを出力する。
	if sys.stdout.isatty():
		if fields == ['name']:
			# デフォルト表示（名前のみ）の場合は端末幅に合わせたグリッド表示を行う
			write_records_as_grid(records)
		else:
			col_widths = { field: get_display_width(field) for field in fields }
			for record in formatted_records:
				for field in fields:
					val = record.get(field, '')
					col_widths[field] = max(col_widths[field], get_display_width(val))
			
			if not no_header:
				header_parts = []
				for field in fields:
					padding = ' ' * (col_widths[field] - get_display_width(field))
					colored_h = f"{ANSI_COLOR_HEADER}{field}{ANSI_COLOR_RESET}"
					header_parts.append(colored_h + padding)
				sys.stdout.write('  '.join(header_parts) + '\n')
			
			for record in formatted_records:
				line_parts = []
				mime_type = record.get('_mimeType', '')
				for field in fields:
					val = record.get(field, '')
					padding = ' ' * (col_widths[field] - get_display_width(val))
					
					# ターミナル出力時は特定フィールドに対しMIMEタイプに基づいた色付けを行う
					if field in ('name', 'relativePath'):
						if mime_type == FOLDER_MIME_TYPE:
							val = f"{ANSI_COLOR_BLUE}{val}{ANSI_COLOR_RESET}"
						elif mime_type == 'application/vnd.google-apps.shortcut':
							val = f"{ANSI_COLOR_CYAN}{val}{ANSI_COLOR_RESET}"
					
					line_parts.append(val + padding)
				sys.stdout.write('  '.join(line_parts) + '\n')
	else:
		# extrasaction='ignore' を指定し、_mimeType などの内部キーが出力されることを防ぐ
		stdout_writer = csv.DictWriter(
			sys.stdout, fieldnames=fields, delimiter='\t', extrasaction='ignore'
		)
		if not no_header:
			stdout_writer.writeheader()
		stdout_writer.writerows(formatted_records)
	
	if not output:
		return
	
	# 出力ファイルが指定されている場合は、ファイルにも書き出す
	output_exists = output.exists()
	output_has_content = output_exists and output.stat().st_size > 0
	
	write_mode = 'a' if append else 'w'
	
	with open(output, write_mode, encoding='utf-8', newline='') as field:
		file_writer = csv.DictWriter(
			field, fieldnames=fields, delimiter='\t', extrasaction='ignore'
		)
		
		# 追記対象が存在しない、または空ファイルの場合は
		# 新しいTSVとしてヘッダーを書き出す。
		should_write_header = not no_header and (
				not append or not output_has_content
		)
		
		if should_write_header:
			file_writer.writeheader()
		
		file_writer.writerows(formatted_records)

def load_json_array(path: Path) -> list[dict]:
	"""JSONファイルから配列を読み込む。

	Args:
		path: JSONファイルのパス。

	Returns:
		JSON配列。

	Raises:
		ValueError: JSONのルート値が配列ではない場合。
		json.JSONDecodeError: JSONを解析できない場合。
	"""
	with open(path, 'r', encoding='utf-8') as file:
		data = json.load(file)
	
	if not isinstance(data, list):
		raise ValueError(f"Cannot append to JSON file because the root value "
							  f"is not an array: {path}")
	
	return data

def write_records_to_json(
		records: list[dict],
		output: Path | None,
		append: bool = False,
) -> None:
	"""レコードを標準出力および指定ファイルへJSON形式で出力する。

	Args:
		records: 書き込むレコード一覧。
		output: 出力ファイルパス。Noneの場合は標準出力のみ。
		append: 既存JSON配列にレコードを追加するかどうか。

	Raises:
		ValueError: 追記対象のJSONが配列ではない場合。
		json.JSONDecodeError: 追記対象のJSONを解析できない場合。
	"""
	# JSONに出力しない内部キーを除外する
	clean_records = [
		{ k: v for k, v in r.items() if not k.startswith('_') }
		for r in records
	]
	
	# 標準出力では現在取得したレコードだけを出力する。
	json_data = json.dumps(clean_records, ensure_ascii=False, indent=2)
	sys.stdout.write(json_data + '\n')
	
	if not output:
		return
	
	records_to_write = clean_records
	
	if append and output.exists() and output.stat().st_size > 0:
		# 新規リストの生成に伴うメモリ負荷を避けるため、既存リストに直接拡張する。
		records_to_write = load_json_array(output)
		records_to_write.extend(clean_records)
	
	file_json_data = json.dumps(
		records_to_write,
		ensure_ascii=False,
		indent=2,
	)
	
	with open(output, 'w', encoding='utf-8') as file:
		file.write(file_json_data + '\n')

def format_describe_record(record: dict, colorize: bool = False) -> str:
	"""レコードを人間向けの詳細表示形式へ変換する。

	Args:
		record: 出力対象レコード。
		colorize: キーに着色を行うかどうか。

	Returns:
		詳細表示用文字列。
	"""
	display_record = { k: v for k, v in record.items() if not k.startswith('_') }
	if not display_record:
		return ''
	
	max_key_len = max(
		(len(key) for key in display_record),
		default=0,
	)
	
	lines = []
	for key, value in display_record.items():
		padded_key = key.ljust(max_key_len)
		if colorize:
			key_str = f"{ANSI_COLOR_HEADER}{padded_key}{ANSI_COLOR_RESET}"
		else:
			key_str = padded_key
		lines.append(f"{key_str} : {value}")
	
	return '\n'.join(lines)

def print_describe_info(
		record: dict,
		output: Path | None = None,
		use_json: bool = False,
		append: bool = False,
) -> None:
	"""単一アイテムの情報を人間向け形式またはJSONで出力する。

	Args:
		record: 出力対象レコード。
		output: 出力ファイルパス。
		use_json: JSON形式を使用するかどうか。
		append: 既存ファイルへ追記するかどうか。
	"""
	clean_record = { k: v for k, v in record.items() if not k.startswith('_') }
	
	if use_json:
		records_to_write = [clean_record]
		
		if append and output and output.exists():
			if output.stat().st_size > 0:
				records_to_write = load_json_array(output) + [clean_record]
		
		output_str = json.dumps(
			records_to_write if append else clean_record,
			ensure_ascii=False,
			indent=2,
		)
		
		sys.stdout.write(output_str + '\n')
		
		if output:
			with open(output, 'w', encoding='utf-8') as f:
				f.write(output_str + '\n')
		
		return
	
	# 標準出力に出力 (着色・列揃えあり)
	stdout_str = format_describe_record(clean_record, colorize=sys.stdout.isatty())
	sys.stdout.write(stdout_str + '\n')
	
	if not output:
		return
	
	# ファイル出力
	file_str = format_describe_record(clean_record, colorize=False)
	
	if append and output.exists() and output.stat().st_size > 0:
		file_str = '\n' + file_str
	
	write_mode = 'a' if append else 'w'
	
	with open(output, write_mode, encoding='utf-8') as file:
		file.write(file_str + '\n')

def list_drive_items(
		service: Resource,
		target_id: str,
		fields: list[str] | None = None,
		include_trashed: bool = False,
		item_mode: bool = False,
		describe_mode: bool = False,
		recursive_mode: bool = False,
		output: str | None = None,
		output_format: str = 'tsv',
		no_header: bool = False,
		append_mode: bool = False,
		quiet: bool = False,
		sort_arg: str | None = None,
) -> None:
	"""指定されたIDのコンテンツを取得し、指定形式で出力する。

	Args:
		service: Google Drive APIサービス。
		target_id: 対象のファイルまたはフォルダID。
		fields: 出力対象のフィールド一覧。
		include_trashed: ゴミ箱内のアイテムを含めるかどうか。
		item_mode: 対象アイテム自身のみを出力するかどうか。
		describe_mode: 対象アイテムを詳細表示するかどうか。
		recursive_mode: 子孫要素まで再帰的に取得するかどうか。
		output: 出力ファイル名。
		output_format: 出力形式。
		no_header: TSVのヘッダーを抑制するかどうか。
		append_mode: 既存ファイルに追記するかどうか。
		quiet: 進捗バーと非エラーログを抑制するかどうか。
		sort_arg: カンマ区切りのソート指定文字列。

	Raises:
		Exception: Google Drive APIで取得に失敗した場合。
	"""
	if fields is not None:
		output_fields = fields
	elif describe_mode:
		output_fields = DEFAULT_LONG_FIELDS
	else:
		output_fields = ['name']
	
	output_path = get_output_path(output) if output else None
	
	# APIに問い合わせる属性を決定する
	root_api_fields, descendant_api_fields = get_required_api_fields(output_fields)
	needs_descendant_agg = any(
		field in AGGREGATIVE_FIELDS for field in output_fields
	)
	is_single_item_mode = item_mode or describe_mode
	
	logger = logging.getLogger(APP_NAME)
	logger.info("Fetching data from Google Drive...")
	
	if is_single_item_mode:
		# 対象アイテムそのものを1つだけ取得する。
		item = service.files().get(
			fileId=target_id,
			fields=', '.join(root_api_fields),
			supportsAllDrives=True,
		).execute()
		
		target_record = create_item_record(
			item, output_fields,
			relative_path=item.get('name', ''), depth=0,
		)
		records_to_output = [target_record]
		
		if needs_descendant_agg and is_folder_item(item):
			# 単一アイテムの集約値を計算するため内部的に子孫を走査するが、出力はルート（対象自身）のみ。
			_, root_records = fetch_records_recursively(
				service,
				item['id'],
				descendant_api_fields,
				output_fields,
				include_trashed,
				quiet=quiet,
			)
			
			# 直下アイテムの集約結果を対象アイテム自身のレコードへ合算する
			for child_record in root_records:
				propagate_to_parent(child_record, target_record, output_fields)
	
	elif recursive_mode or needs_descendant_agg:
		# 再帰探索、集約、レコード構築を同じ走査で実行する。
		# recursive_modeがFalseの場合でも、集約値計算のために子孫を取得するが、
		# その場合の最終的な出力対象は直下アイテム（root_records）のみに絞り込む。
		all_records, root_records = fetch_records_recursively(
			service,
			target_id,
			root_api_fields,
			output_fields,
			include_trashed,
			quiet=quiet,
			needs_descendant_agg=needs_descendant_agg,
		)
		
		records_to_output = all_records if recursive_mode else root_records
	
	else:
		# 集約が不要な場合は、指定フォルダの直下要素だけを取得する。
		root_items = fetch_children(
			service, target_id, root_api_fields, include_trashed,
		)
		
		records_to_output = [
			create_item_record(
				child, output_fields,
				relative_path=child.get('name', ''), depth=1,
			)
			for child in root_items
		]
	
	# 直下要素が0件だった場合、対象ID自体が非フォルダである可能性がある。
	if not is_single_item_mode and not records_to_output:
		# 対象が非フォルダであった場合に自身のレコードを構築するため、必要なフィールドを網羅して取得する
		all_required_fields = list(set(root_api_fields + descendant_api_fields))
		target_info = service.files().get(
			fileId=target_id,
			fields=', '.join(all_required_fields),
			supportsAllDrives=True,
		).execute()
		
		if not is_folder_item(target_info):
			# フォルダでない場合は、Linuxのlsコマンドの慣習に合わせて対象アイテム自身を出力結果とする
			records_to_output = [
				create_item_record(
					target_info, output_fields,
					relative_path=target_info.get('name', ''), depth=0,
				)
			]
	
	if sort_arg and not is_single_item_mode:
		sort_records(records_to_output, sort_arg)
	
	# === 結果を出力する ===
	if describe_mode:
		if records_to_output:
			print_describe_info(
				records_to_output[0],
				output_path,
				use_json=(output_format == 'json'),
				append=append_mode,
			)
	
	elif output_format == 'json':
		write_records_to_json(
			records=records_to_output,
			output=output_path,
			append=append_mode,
		)
	
	else:
		write_records_to_tsv(
			records=records_to_output,
			fields=output_fields,
			output=output_path,
			append=append_mode,
			no_header=no_header,
		)
	
	logger.info(f"Items: {len(records_to_output)}")
	if output_path:
		logger.info(f"Output to: {output_path}")

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
	parser.add_argument('--include-trashed', action='store_true',
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
	parser.add_argument('-a', '--append', action='store_true',
							  help="Append to existing output file")
	
	# 出力形式
	parser.add_argument('-j', '--json', action='store_true',
							  help="Output in JSON format (instead of TSV)")
	
	# 出力形式オプション
	parser.add_argument('--no-header', action='store_true',
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
	
	if args.no_header and args.json:
		raise ValueError("The '--no-header' option can only be used with TSV output.")
	
	if args.describe and args.no_header:
		raise ValueError("The '--no-header' option cannot be used with '--describe'.")

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
	
	output_format = 'json' if args.json else 'tsv'
	
	try:
		service = get_drive_service(
			client_secret_file=args.client_secret,
			token_file=args.token_file,
		)
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
	except Exception as exc:
		logging.error(f"An unexpected error occurred: {exc}")
		return 1
	
	try:
		list_drive_items(
			service,
			target_id,
			fields=output_fields,
			include_trashed=args.include_trashed,
			item_mode=args.item,
			describe_mode=args.describe,
			recursive_mode=args.recursive,
			output_format=output_format,
			no_header=args.no_header,
			output=args.output,
			append_mode=args.append,
			quiet=args.quiet,
			sort_arg=args.sort,
		)
	except KeyboardInterrupt:
		logging.error("Operation cancelled by user.")
		return 130
	except Exception as exc:
		logging.error(f"Failed to retrieve Google Drive data: {exc}")
		logging.debug(
			"Detailed exception information:",
			exc_info=True,
		)
		return 1
	
	return 0

if __name__ == '__main__':
	sys.exit(main())
