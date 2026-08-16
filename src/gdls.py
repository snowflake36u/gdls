import argparse
import csv
import json
import logging
import re
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

# デフォルトの出力属性定義
LONG_OUTPUT_HEADERS = [
	'permissions',
	'owners',
	'size',
	'modifiedTime',
	'id',
	'name',
]

# 出力属性と必要なAPIフィールドの対応マッピング
HEADER_API_DEPENDENCIES: dict[str, list[str]] = {
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

def extract_folder_id(url_or_id: str) -> str:
	"""URLまたはID文字列からフォルダIDのみを抽出する。

	Args:
		url_or_id: Google DriveのURLまたはファイル・フォルダID。

	Returns:
		抽出されたID文字列。
	"""
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
		output_headers: list[str],
) -> tuple[list[str], list[str]]:
	"""出力対象ヘッダーに必要なAPIフィールドを決定する。

	Args:
		output_headers: 出力対象ヘッダー。

	Returns:
		ルートアイテム用フィールドと子孫アイテム用フィールドのタプル。
	"""
	root_field_set = { 'id', 'mimeType', 'name' }
	descendant_field_set = set(root_field_set)
	
	for header in output_headers:
		# APIフィールド依存が定義されていればそれを採用し、
		# なければ属性名をそのままAPIフィールド名として扱う。
		dependencies = HEADER_API_DEPENDENCIES.get(header, [header])
		
		root_field_set.update(dependencies)
		
		if header in AGGREGATIVE_FIELDS:
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
		item_record, parent_record, output_headers
):
	if 'oldestCreatedTime' in output_headers:
		item_oldest = item_record['oldestCreatedTime']
		parent_oldest = parent_record['oldestCreatedTime']
		
		# 最古作成日時の更新（文字列の辞書順比較によって判別）
		if item_oldest and (
				not parent_oldest or item_oldest < parent_oldest
		):
			parent_record['oldestCreatedTime'] = item_oldest
	
	if 'totalSize' in output_headers:
		parent_record['totalSize'] += item_record['totalSize']
	
	if 'totalQuotaBytesUsed' in output_headers:
		parent_record['totalQuotaBytesUsed'] += item_record['totalQuotaBytesUsed']

def fetch_records_recursively(
		service: Resource,
		root_folder_id: str,
		api_fields: list[str],
		output_headers: list[str],
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
		output_headers: 出力対象のヘッダー。
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
				child, output_headers,
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
							output_headers,
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
						desc, output_headers,
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
		headers: list[str],
		relative_path: str | None = None,
		depth: int = 0,
) -> dict:
	"""フォルダアイテムのレコードを構築する。

	指定された出力ヘッダーに基づき、APIレスポンスから必要な属性を抽出・整形する。

	Args:
		item: APIから取得したアイテム。
		headers: 出力対象のヘッダー一覧。
		relative_path: アイテムのルートからの相対パス。
		depth: アイテムの階層深度。

	Returns:
		構築されたレコード辞書。集約対象の値は探索完了時に更新される。
	"""
	owner_name = get_owner_name(item)
	
	# レコードを構築
	record = { }
	for header in headers:
		if header == 'owners':
			record[header] = owner_name
		elif header == 'permissions':
			record[header] = get_permissions_string(item)
		elif header == 'oldestCreatedTime':
			# 初期値は自身の作成日時とし、探索完了後に子孫を含む集約値へ更新する。
			record[header] = item.get('createdTime', '')
		elif header == 'totalSize':
			# 初期値は自身のサイズとし、探索完了後に子孫を含む集約値へ更新する。
			record[header] = int(item.get('size') or 0)
		elif header == 'totalQuotaBytesUsed':
			# 初期値は自身の使用量とし、探索完了後に子孫を含む集約値へ更新する。
			record[header] = int(item.get('quotaBytesUsed') or 0)
		elif header == 'relativePath':
			record[header] = item.get('name', '') if relative_path is None else relative_path
		elif header == 'depth':
			record[header] = depth
		elif header == 'parents':
			# API仕様上、親IDのリストとして返却されるため直接カンマ区切りで結合する。
			record[header] = ','.join(item.get('parents', []))
		else:
			# APIが返した値をそのまま設定する
			record[header] = item.get(header, '')
	
	return record

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

def write_records_to_tsv(
		records: list[dict],
		headers: list[str],
		output: Path | None,
		append: bool = False,
		no_header: bool = False,
) -> None:
	"""レコードを標準出力と指定ファイルにTSV形式で出力する。

	Args:
		records: 書き込むレコード一覧。
		headers: ヘッダー一覧。
		output: 出力ファイルパス。Noneの場合は標準出力のみ。
		append: 既存ファイルへの追記を行うかどうか。
		no_header: ヘッダー行を出力しないかどうか。
	"""
	# 複雑なオブジェクト型の値を文字列化してフォーマット崩れを防ぐ
	formatted_records = [
		{ k: format_cell_for_tsv(v) for k, v in record.items() }
		for record in records
	]
	
	# 実行環境がコンソールの場合は列を揃えて見やすく表示し、
	# パイプやリダイレクトの場合は純粋なTSVを出力する。
	if sys.stdout.isatty():
		col_widths = { h: get_display_width(h) for h in headers }
		for record in formatted_records:
			for h in headers:
				val = record.get(h, '')
				col_widths[h] = max(col_widths[h], get_display_width(val))
		
		if not no_header:
			header_parts = []
			for h in headers:
				padding = ' ' * (col_widths[h] - get_display_width(h))
				header_parts.append(h + padding)
			sys.stdout.write('  '.join(header_parts) + '\n')
		
		for record in formatted_records:
			line_parts = []
			for h in headers:
				val = record.get(h, '')
				padding = ' ' * (col_widths[h] - get_display_width(val))
				line_parts.append(val + padding)
			sys.stdout.write('  '.join(line_parts) + '\n')
	else:
		stdout_writer = csv.DictWriter(sys.stdout, fieldnames=headers, delimiter='\t')
		if not no_header:
			stdout_writer.writeheader()
		stdout_writer.writerows(formatted_records)
	
	if not output:
		return
	
	# 出力ファイルが指定されている場合は、ファイルにも書き出す
	output_exists = output.exists()
	output_has_content = output_exists and output.stat().st_size > 0
	
	write_mode = 'a' if append else 'w'
	
	with open(output, write_mode, encoding='utf-8', newline='') as f:
		file_writer = csv.DictWriter(f, fieldnames=headers, delimiter='\t')
		
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
	# 標準出力では現在取得したレコードだけを出力する。
	json_data = json.dumps(records, ensure_ascii=False, indent=2)
	sys.stdout.write(json_data + '\n')
	
	if not output:
		return
	
	records_to_write = records
	
	if append and output.exists() and output.stat().st_size > 0:
		# 新規リストの生成に伴うメモリ負荷を避けるため、既存リストに直接拡張する。
		records_to_write = load_json_array(output)
		records_to_write.extend(records)
	
	file_json_data = json.dumps(
		records_to_write,
		ensure_ascii=False,
		indent=2,
	)
	
	with open(output, 'w', encoding='utf-8') as file:
		file.write(file_json_data + '\n')

def format_describe_record(record: dict) -> str:
	"""レコードを人間向けの詳細表示形式へ変換する。

	Args:
		record: 出力対象レコード。

	Returns:
		詳細表示用文字列。
	"""
	max_key_len = max(
		(len(key) for key in record),
		default=0,
	)
	
	return '\n'.join(
		f"{key.ljust(max_key_len)} : {value}"
			for key, value in record.items()
	)

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
	if use_json:
		records_to_write = [record]
		
		if append and output and output.exists():
			if output.stat().st_size > 0:
				records_to_write = load_json_array(output) + [record]
		
		output_str = json.dumps(
			records_to_write if append else record,
			ensure_ascii=False,
			indent=2,
		)
		
		sys.stdout.write(output_str + '\n')
		
		if output:
			with open(output, 'w', encoding='utf-8') as f:
				f.write(output_str + '\n')
		
		return
	
	output_str = format_describe_record(record)
	
	# 標準出力に出力
	sys.stdout.write(output_str + '\n')
	
	if not output:
		return
	
	# ファイル出力
	if append and output.exists() and output.stat().st_size > 0:
		output_str = '\n' + output_str
	
	write_mode = 'a' if append else 'w'
	
	with open(output, write_mode, encoding='utf-8') as file:
		file.write(output_str + '\n')

def list_drive_folder(
		service: Resource,
		target_folder_id: str,
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
) -> None:
	"""フォルダ内の情報を取得し、指定形式で出力する。

	Args:
		service: Google Drive APIサービス。
		target_folder_id: 対象フォルダID。
		fields: 出力対象のヘッダー。
		include_trashed: ゴミ箱内のアイテムを含めるかどうか。
		item_mode: 対象アイテム自身のみを出力するかどうか。
		describe_mode: 対象アイテムを詳細表示するかどうか。
		recursive_mode: 子孫要素まで再帰的に取得するかどうか。
		output_format: 出力形式。
		no_header: TSVのヘッダーを抑制するかどうか。
		output: 出力ファイル名。
		append_mode: 既存ファイルに追記するかどうか。
		quiet: 進捗バーと非エラーログを抑制するかどうか。

	Raises:
		Exception: Google Drive APIで取得に失敗した場合。
	"""
	if fields is not None:
		output_headers = fields
	elif describe_mode:
		output_headers = LONG_OUTPUT_HEADERS
	else:
		output_headers = ['name']
	
	output_path = get_output_path(output) if output else None
	
	# APIに問い合わせる属性を決定する
	root_api_fields, descendant_api_fields = get_required_api_fields(output_headers)
	needs_descendant_agg = any(
		header in AGGREGATIVE_FIELDS for header in output_headers
	)
	is_single_item_mode = item_mode or describe_mode
	
	logger = logging.getLogger(APP_NAME)
	logger.info("Fetching data from Google Drive...")
	
	if is_single_item_mode:
		# 対象アイテムそのものを1つだけ取得する。
		item = service.files().get(
			fileId=target_folder_id,
			fields=', '.join(root_api_fields),
			supportsAllDrives=True,
		).execute()
		
		target_record = create_item_record(
			item, output_headers,
			relative_path=item.get('name', ''), depth=0,
		)
		records_to_output = [target_record]
		
		if needs_descendant_agg and is_folder_item(item):
			# 単一アイテムの集約値を計算するため内部的に子孫を走査するが、出力はルート（対象自身）のみ。
			_, root_records = fetch_records_recursively(
				service,
				item['id'],
				descendant_api_fields,
				output_headers,
				include_trashed,
				quiet=quiet,
			)
			
			# 直下アイテムの集約結果を対象アイテム自身のレコードへ合算する
			for child_record in root_records:
				propagate_to_parent(child_record, target_record, output_headers)
	
	elif recursive_mode or needs_descendant_agg:
		# 再帰探索、集約、レコード構築を同じ走査で実行する。
		# recursive_modeがFalseの場合でも、集約値計算のために子孫を取得するが、
		# その場合の最終的な出力対象は直下アイテム（root_records）のみに絞り込む。
		all_records, root_records = fetch_records_recursively(
			service,
			target_folder_id,
			root_api_fields,
			output_headers,
			include_trashed,
			quiet=quiet,
			needs_descendant_agg=needs_descendant_agg,
		)
		
		records_to_output = all_records if recursive_mode else root_records
	
	else:
		# 集約が不要な場合は、指定フォルダの直下要素だけを取得する。
		root_items = fetch_children(
			service, target_folder_id, root_api_fields, include_trashed,
		)
		
		records_to_output = [
			create_item_record(
				child, output_headers,
				relative_path=child.get('name', ''), depth=1,
			)
			for child in root_items
		]
	
	# 直下要素が0件だった場合、対象ID自体が非フォルダである可能性がある。
	if not is_single_item_mode and not records_to_output:
		# 対象が非フォルダであった場合に自身のレコードを構築するため、必要なフィールドを網羅して取得する
		all_required_fields = list(set(root_api_fields + descendant_api_fields))
		target_info = service.files().get(
			fileId=target_folder_id,
			fields=', '.join(all_required_fields),
			supportsAllDrives=True,
		).execute()
		
		if not is_folder_item(target_info):
			# フォルダでない場合は、Linuxのlsコマンドの慣習に合わせて対象アイテム自身を出力結果とする
			records_to_output = [
				create_item_record(
					target_info, output_headers,
					relative_path=target_info.get('name', ''), depth=0,
				)
			]
	
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
			headers=output_headers,
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
		description="Export Google Drive folder contents to TSV or JSON"
	)
	parser.add_argument('target',
							  help="Google Drive folder URL or folder ID")
	
	# 探索範囲
	parser.add_argument('-R', '--recursive', action='store_true',
							  help="Recursively list items in subfolders")
	parser.add_argument('--include-trashed', action='store_true',
							  help="Include trashed files in the calculation and output")
	
	# 単一アイテムモード
	item_group = parser.add_mutually_exclusive_group()
	item_group.add_argument('-s', '--item', action='store_true',
									help="Fetch and output information ONLY for the specified target file/folder itself")
	item_group.add_argument('--describe', action='store_true',
									help="Display detailed information for a single target item in a readable format")
	
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
	parser.add_argument('--json', action='store_true',
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

def parse_output_headers(
		fields_arg: str | None,
) -> list[str] | None:
	"""フィールド引数を解析して出力ヘッダーを取得する。

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
	logging.basicConfig(
		level=numeric_level, format='%(message)s', stream=sys.stderr
	)
	
	try:
		validate_arguments(args)
	except ValueError as exc:
		logging.error(f"Error: {exc}")
		return 2
	
	target_id = extract_folder_id(args.target)
	if not target_id:
		logging.error("Error: Unable to extract a valid folder ID.")
		return 2
	
	if args.long:
		output_headers = LONG_OUTPUT_HEADERS
	else:
		output_headers = parse_output_headers(args.fields)
	
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
		list_drive_folder(
			service,
			target_id,
			fields=output_headers,
			include_trashed=args.include_trashed,
			item_mode=args.item,
			describe_mode=args.describe,
			recursive_mode=args.recursive,
			output_format=output_format,
			no_header=args.no_header,
			output=args.output,
			append_mode=args.append,
			quiet=args.quiet,
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
