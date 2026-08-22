from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys

import unicodedata
from pathlib import Path
from typing import Any, TextIO

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from progress_reporter import ProgressReporter, ProgressEvent, NullProgressReporter
from progress_reporter.tqdm_reporter import TqdmProgressReporter
from progress_reporter.trigger import IntervalTrigger

from .config import (
	APP_NAME,
	ensure_directories,
	get_client_secret_file,
	get_output_path,
	get_token_file,
)

# === Future Location: auth.py ===

# スコープの設定（読み取り専用）
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

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
			creds = flow.run_local_server(port=0)
		with open(token_path, 'w', encoding='utf-8') as token:
			token.write(creds.to_json())
	return build('drive', 'v3', credentials=creds)

# === Future Location: models.py ===

class DriveItem:
	"""Google Drive APIから取得したアイテムを表現する。"""
	
	def __init__(self, data: dict[str, Any]) -> None:
		self._data = data
		# コンソール出力時の色付け判定等に使用する内部用メタデータ
		self._mime_type = data.get('mimeType', '')
	
	@property
	def mime_type(self) -> str:
		return self._mime_type
	
	def is_folder(self) -> bool:
		return self._mime_type == 'application/vnd.google-apps.folder'
	
	def is_shortcut(self) -> bool:
		return self._mime_type == 'application/vnd.google-apps.shortcut'
	
	def __getitem__(self, key: str) -> Any:
		return self._data[key]
	
	def __setitem__(self, key: str, value: Any) -> None:
		self._data[key] = value
	
	def get(self, key: str, default: Any = None) -> Any:
		return self._data.get(key, default)
	
	def items(self):
		yield from self._data.items()

# === Future Location: repository.py ===
# ドライブAPIを用いたクエリを担う

class DriveRepository:
	"""Google Drive APIとの通信およびデータ取得を担当するリポジトリクラス。"""
	
	def __init__(self, service: Resource) -> None:
		self._service = service
	
	@property
	def service(self) -> Resource:
		"""Google Drive APIサービスインスタンスを取得する。"""
		return self._service
	
	def fetch_item(self, item_id: str, api_fields: list[str]) -> DriveItem:
		"""指定されたIDの単一アイテム情報を取得する。

		Args:
			item_id: 対象のファイルまたはフォルダID。
			api_fields: 取得するAPIフィールド一覧。

		Returns:
			アイテム情報の辞書。
		"""
		return DriveItem(self._service.files().get(
			fileId=item_id,
			fields=', '.join(api_fields),
			supportsAllDrives=True,
		).execute())
	
	def fetch_children(
			self,
			folder_id: str,
			api_fields: list[str],
			include_trashed: bool = False,
	) -> list[DriveItem]:
		"""指定フォルダの直下の要素一覧を取得する。

		APIのページネーションに従い、要素がなくなるまで反復して取得する。

		Args:
			folder_id: 対象フォルダID。
			api_fields: APIから取得するフィールド。
			include_trashed: ゴミ箱内の要素を含めるかどうか。

		Returns:
			直下のアイテム一覧。
		"""
		trashed_query = '' if include_trashed else ' and trashed=false'
		query = f"'{folder_id}' in parents{trashed_query}"
		fields = f"nextPageToken, files({', '.join(api_fields)})"
		
		children: list[DriveItem] = []
		page_token = None
		
		while True:
			response = self._service.files().list(
				q=query,
				spaces='drive',
				fields=fields,
				pageToken=page_token,
				includeItemsFromAllDrives=True,
				supportsAllDrives=True,
			).execute()
			
			children.extend(map(DriveItem, response.get('files', [])))
			
			page_token = response.get('nextPageToken')
			if not page_token:
				break
		
		return children

# === Future Location: scanner.py ===

# 出力フィールドと必要なAPIフィールドの対応マッピング
FIELD_API_DEPENDENCIES: dict[str, list[str]] = {
	'oldestCreatedTime': ['createdTime'],
	'latestCreatedTime': ['createdTime'],
	'oldestModifiedTime': ['modifiedTime'],
	'latestModifiedTime': ['modifiedTime'],
	'totalSize': ['size'],
	'totalQuotaBytesUsed': ['quotaBytesUsed'],
	'relativePath': ['name'],
	'depth': [],
	'permissions': ['mimeType', 'capabilities/canEdit', 'shared'],
	'itemCount': ['mimeType'],
	'fileCount': ['mimeType'],
	'folderCount': ['mimeType'],
	'childItemCount': ['mimeType'],
	'childFileCount': ['mimeType'],
	'childFolderCount': ['mimeType'],
}

# 子孫要素の取得・集約が必要な属性一覧
AGGREGATIVE_FIELDS: set[str] = {
	'oldestCreatedTime',  # 自分自身および子孫アイテムのうち最古の作成日時
	'latestCreatedTime',  # 自分自身および子孫アイテムのうち最新の作成日時
	'oldestModifiedTime',  # 自分自身および子孫アイテムのうち最古の更新日時
	'latestModifiedTime',  # 自分自身および子孫アイテムのうち最新の更新日時
	'totalSize',
	'totalQuotaBytesUsed',
	'itemCount',
	'fileCount',
	'folderCount',
	'childItemCount',
	'childFileCount',
	'childFolderCount',
}

# 数値として評価・ソートすべき非文字列属性および数値属性
NUMERIC_FIELDS: set[str] = {
	'size',
	'quotaBytesUsed',
	'version',
	'depth',
	'itemCount',
	'fileCount',
	'folderCount',
	'childItemCount',
	'childFileCount',
	'childFolderCount',
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

class ItemTreeScanner:
	"""Google Driveのディレクトリツリー探索、レコード構築および集約計算を担当するクラス。"""
	
	def __init__(self, repository: DriveRepository) -> None:
		self._repository = repository
	
	def build_record(
			self,
			item: DriveItem,
			fields: list[str],
			relative_path: str | None = None,
			depth: int = 0,
	) -> DriveItem:
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
		for field in fields:
			match field:
				case 'owners':
					item[field] = self._get_owner_name(item)
				case 'permissions':
					item[field] = self._get_permissions_string(item)
				case 'oldestCreatedTime' | 'latestCreatedTime':
					# 初期値は自身の作成日時とし、探索完了後に子孫を含む集約値へ更新する。
					item[field] = item.get('createdTime', '')
				case 'oldestModifiedTime' | 'latestModifiedTime':
					# 初期値は自身の更新日時とし、探索完了後に子孫を含む集約値へ更新する。
					item[field] = item.get('modifiedTime', '')
				case 'totalSize':
					# 初期値は自身のサイズとし、探索完了後に子孫を含む集約値へ更新する。
					item[field] = int(item.get('size') or 0)
				case 'totalQuotaBytesUsed':
					# 初期値は自身の使用量とし、探索完了後に子孫を含む集約値へ更新する。
					item[field] = int(item.get('quotaBytesUsed') or 0)
				case 'itemCount' | 'fileCount' | 'folderCount' | 'childItemCount' | 'childFileCount' | 'childFolderCount':
					# 初期値は0とし、探索完了後に子孫を含む集約値へ更新する。
					item[field] = 0
				case 'relativePath':
					item[field] = item.get('name', '') if relative_path is None else relative_path
				case 'depth':
					item[field] = depth
				case 'parents':
					# API仕様上、親IDのリストとして返却されるため直接カンマ区切りで結合する。
					item[field] = ','.join(item.get('parents', []))
				case _:
					# APIが返した値をそのまま設定する
					item[field] = item.get(field, '')
		
		return item
	
	@staticmethod
	def _get_owner_name(item: DriveItem) -> str:
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
	
	@staticmethod
	def _get_permissions_string(item: DriveItem) -> str:
		"""アイテムからlsコマンドのようなパーミッション文字列を生成する。
		
		ファイルタイプ(1文字)、読み書き実行の権限(3文字)、共有ステータス(1文字)を
		組み合わせた5文字の文字列を構築する。
		
		Args:
			item: Google Driveアイテム。
		
		Returns:
			フォーマットされたパーミッション文字列。
		"""
		if item.is_folder():
			type_char = 'd'
		elif item.is_shortcut():
			type_char = 'l'
		else:
			type_char = '-'
		
		capabilities = item.get('capabilities', { })
		can_edit = capabilities.get('canEdit', False)
		
		# API経由でアイテム情報を取得できている時点で、読み取り権限(Read)は必ず存在する。
		read_char = 'r'
		write_char = 'w' if can_edit else '-'
		# フォルダとして認識されるものはディレクトリ移動可能とみなして 'x' を付与する
		exec_char = 'x' if type_char == 'd' else '-'
		shared_char = '+' if item.get('shared', False) else '-'
		
		return f'{type_char}{read_char}{write_char}{exec_char}{shared_char}'
	
	def scan(
			self,
			root_folder_id: str,
			api_fields: list[str],
			output_fields: list[str],
			include_trashed: bool = False,
			needs_descendant_agg: bool = True,
			reporter: ProgressReporter | None = None,
	) -> tuple[list[DriveItem], list[DriveItem]]:
		"""指定フォルダ配下のすべての要素を反復処理で全件取得し、
		データの集約とレコード構築を行う。
		
		関数呼び出しのオーバーヘッド削減と、深くネストされたディレクトリ構造での
		再帰呼び出し上限（RecursionError）を回避するため、内部的にスタック構造を
		用いた深さ優先探索（DFS）アルゴリズムを採用している。
		
		Args:
			root_folder_id: 起点フォルダID。
			api_fields: APIから取得するフィールド。
			output_fields: 出力対象のフィールド。
			include_trashed: ゴミ箱内の要素を含めるかどうか。
			needs_descendant_agg: 子孫集約値を計算するかどうか。
			reporter: 進捗表示を管理するリポータ。

		Returns:
			構築済みレコードのリストと、直下アイテムのレコードのリストのタプル。
		"""
		root_children = self._repository.fetch_children(
			root_folder_id, api_fields, include_trashed,
		)
		
		records: list[DriveItem] = []
		root_records: list[DriveItem] = []
		
		# 集計処理のために、IDベースでレコードや親子関係を参照する辞書
		records_by_id: dict[str, DriveItem] = { }
		parent_ids: dict[str, str | None] = { }
		
		reporter = reporter or NullProgressReporter()
		
		progress = reporter.watch(root_children)
		for child in progress:
			# 直下アイテムのレコードを作成する
			child_name = child.get('name', '')
			child_id = child['id']
			child_depth = 1
			
			child_record = self.build_record(
				child, output_fields,
				relative_path=child_name, depth=child_depth,
			)
			records.append(child_record)
			records_by_id[child_id] = child_record
			root_records.append(child_record)
			parent_ids[child_id] = root_folder_id
			
			if not child_record.is_folder():
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
					parent_id = parent_ids.get(current_id)
					parent_record = records_by_id.get(parent_id) if parent_id else None
					
					if parent_record:
						self.propagate_to_parent(
							records_by_id[current_id],
							parent_record,
							output_fields,
						)
					continue
				
				# =====
				# 行きがけの処理
				# 対象フォルダ内のアイテムを取得し、スタックに追加する。
				# =====
				descendants = self._repository.fetch_children(
					current_id, api_fields, include_trashed,
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
					
					desc_record = self.build_record(
						desc, output_fields,
						relative_path=relative_path, depth=desc_depth,
					)
					records.append(desc_record)
					records_by_id[desc['id']] = desc_record
					parent_ids[desc['id']] = current_id
					
					if desc_record.is_folder():
						# 子アイテムがフォルダの場合は、さらにその内部を走査するためスタックに積む（行きがけ処理）
						stack.append((desc['id'], relative_path, desc_depth, False))
					elif needs_descendant_agg:
						# 子アイテムがファイルで、集約が必要な場合は、自身の情報を親へ伝播させるため帰りがけ処理のみ積む
						stack.append((desc['id'], relative_path, desc_depth, True))
					
					# 子孫アイテムの処理完了を報告
					progress.update(0, descendant_increment=1)
		
		return records, root_records
	
	@staticmethod
	def propagate_to_parent(
			item_record: DriveItem,
			parent_record: DriveItem,
			output_fields: list[str],
	) -> None:
		"""子要素の集約値を親要素のレコードへ加算・伝播する。

		Args:
			item_record: 子要素のレコード。
			parent_record: 親要素のレコード。
			output_fields: 出力対象のフィールド一覧。
		"""
		for field in output_fields:
			match field:
				case 'oldestCreatedTime' | 'oldestModifiedTime':
					# ISO 8601形式の文字列辞書順を利用して最古日時を更新する
					item_val = item_record.get(field)
					parent_val = parent_record.get(field)
					
					if item_val and (not parent_val or item_val < parent_val):
						# parent_recordのディクショナリを更新
						parent_record[field] = item_val
				
				case 'latestCreatedTime' | 'latestModifiedTime':
					# ISO 8601形式の文字列辞書順を利用して最新日時を更新する
					item_val = item_record.get(field)
					parent_val = parent_record.get(field)
					
					if item_val and (not parent_val or item_val > parent_val):
						parent_record[field] = item_val
				
				case 'totalSize' | 'totalQuotaBytesUsed':
					parent_record[field] += item_record.get(field, 0)
				
				case 'itemCount':
					# 自身（1）と、自身が持つ子孫の数を加算
					parent_record[field] += 1 + item_record.get('itemCount', 0)
				
				case 'fileCount':
					self_count = 0 if item_record.is_folder() else 1
					parent_record[field] += self_count + item_record.get('fileCount', 0)
				
				case 'folderCount':
					self_count = 1 if item_record.is_folder() else 0
					parent_record[field] += self_count + item_record.get('folderCount', 0)
				
				case 'childItemCount':
					# 直下の子アイテム数として自身（1）のみを加算
					parent_record[field] += 1
				
				case 'childFileCount':
					if not item_record.is_folder():
						parent_record[field] += 1
				
				case 'childFolderCount':
					if item_record.is_folder():
						parent_record[field] += 1

# === Future Location: exporter.py ===

# コンソール出力用のANSIエスケープコード
FOLDER_COLOR = '\033[1;34m'
SHORTCUT_COLOR = '\033[1;36m'
ANSI_COLOR_HEADER = '\033[1;33m'
ANSI_COLOR_RESET = '\033[0m'

class OutputFormatter:
	"""コンソール表示および各種フォーマット処理を行うユーティリティクラス。"""
	
	@staticmethod
	def get_display_width(text: str) -> int:
		"""文字列の表示幅を計算する。
		
		コンソール等で等幅フォントを使用した場合の視覚的な文字幅を算出する。
		全角文字は2、半角文字は1として計算する。
		
		Args:
			text: 対象文字列。
		
		Returns:
			表示幅。
		"""
		width = 0
		for char in text:
			if unicodedata.east_asian_width(char) in ('F', 'W', 'A'):
				width += 2
			else:
				width += 1
		return width
	
	@staticmethod
	def format_grid_item(record: DriveItem) -> tuple[str, int]:
		"""グリッド出力用にアイテム名と表示幅を取得し、端末出力時は色付けを行う。
		
		Args:
			record: Google Driveアイテムのレコード。
		
		Returns:
			フォーマット済み表示名と計算された表示幅のタプル。
		"""
		name = str(record.get('name', ''))
		width = OutputFormatter.get_display_width(name)
		
		if not sys.stdout.isatty():
			return name, width
		
		if record.is_folder():
			formatted = f"{FOLDER_COLOR}{name}{ANSI_COLOR_RESET}"
		elif record.is_shortcut():
			formatted = f"{SHORTCUT_COLOR}{name}{ANSI_COLOR_RESET}"
		else:
			formatted = name
		
		return formatted, width
	
	@staticmethod
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
	
	@staticmethod
	def format_describe_record(record: dict[str, Any], colorize: bool = False) -> str:
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
		
		max_key_len = max(map(len, display_record.keys()), default=0)
		
		lines = []
		for key, value in display_record.items():
			padded_key = key.ljust(max_key_len)
			if colorize:
				key_str = f"{ANSI_COLOR_HEADER}{padded_key}{ANSI_COLOR_RESET}"
			else:
				key_str = padded_key
			lines.append(f"{key_str} : {value}")
		
		return '\n'.join(lines)

class RecordExporter:
	"""取得・構築されたレコードを各種形式（TSV, JSON, 詳細形式）で出力するクラス。"""
	
	def __init__(self, formatter: OutputFormatter | None = None) -> None:
		self._formatter = formatter or OutputFormatter()
	
	def export_grid(self, records: list[DriveItem]) -> None:
		"""端末の表示幅に合わせて要素を列優先のグリッド形式で標準出力へ表示する。
		
		Args:
			records: 出力対象のレコード一覧。
		"""
		if not records:
			return
		
		formatted_items = [self._formatter.format_grid_item(record) for record in records]
		max_name_width = max(map(lambda item: item[1], formatted_items), default=0)
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
	
	def export_tsv(
			self,
			records: list[DriveItem],
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
			{ k: self._formatter.format_cell_for_tsv(v) for k, v in record.items() }
			for record in records
		]
		
		# 実行環境がコンソールの場合は視認性を考慮したフォーマットを行い、
		# パイプやリダイレクトの場合は機械処理に適したTSVを出力する。
		if sys.stdout.isatty():
			if fields == ['name']:
				# デフォルト表示（名前のみ）の場合は端末幅に合わせたグリッド表示を行う
				self.export_grid(records)
			else:
				self._export_tty_tsv(records, formatted_records, fields, no_header)
		else:
			self._export_csv_stream(sys.stdout, formatted_records, fields, no_header)
		
		if not output:
			return
		
		self._export_to_file(output, formatted_records, fields, append, no_header)
	
	def _export_tty_tsv(
			self,
			records: list[DriveItem],
			formatted_records: list[dict[str, str]],
			fields: list[str],
			no_header: bool,
	) -> None:
		"""端末出力向けに列幅を揃え、必要に応じて色付けを行ったTSV形式を出力する。"""
		col_widths = { field: self._formatter.get_display_width(field) for field in fields }
		for f_data in formatted_records:
			for field in fields:
				val = f_data.get(field, '')
				col_widths[field] = max(col_widths[field], self._formatter.get_display_width(val))
		
		if not no_header:
			header_parts = []
			for field in fields:
				padding = ' ' * (col_widths[field] - self._formatter.get_display_width(field))
				colored_h = f"{ANSI_COLOR_HEADER}{field}{ANSI_COLOR_RESET}"
				header_parts.append(colored_h + padding)
			sys.stdout.write('  '.join(header_parts) + '\n')
		
		for record, f_data in zip(records, formatted_records):
			line_parts = []
			for field in fields:
				val = f_data.get(field, '')
				padding = ' ' * (col_widths[field] - self._formatter.get_display_width(val))
				
				# ターミナル出力時は特定フィールドに対しMIMEタイプに基づいた色付けを行う
				if field in ('name', 'relativePath'):
					if record.is_folder():
						val = f"{FOLDER_COLOR}{val}{ANSI_COLOR_RESET}"
					elif record.is_shortcut():
						val = f"{SHORTCUT_COLOR}{val}{ANSI_COLOR_RESET}"
				
				line_parts.append(val + padding)
			sys.stdout.write('  '.join(line_parts) + '\n')
	
	def _export_csv_stream(
			self,
			stream: TextIO,
			formatted_records: list[dict[str, str]],
			fields: list[str],
			no_header: bool,
	) -> None:
		"""指定されたストリームに対して機械処理用のTSVを出力する。"""
		# extrasaction='ignore' を指定し、_mimeType などの内部キーが出力されることを防ぐ
		writer = csv.DictWriter(
			stream, fieldnames=fields, delimiter='\t', extrasaction='ignore',
			lineterminator='\n'
		)
		if not no_header:
			writer.writeheader()
		writer.writerows(formatted_records)
	
	def _export_to_file(
			self,
			output: Path,
			formatted_records: list[dict[str, str]],
			fields: list[str],
			append: bool,
			no_header: bool,
	) -> None:
		"""ファイルに対してTSVを出力する。"""
		output_exists = output.exists()
		output_has_content = output_exists and output.stat().st_size > 0
		write_mode = 'a' if append else 'w'
		
		with open(output, write_mode, encoding='utf-8', newline='') as f:
			file_writer = csv.DictWriter(
				f, fieldnames=fields, delimiter='\t', extrasaction='ignore'
			)
			
			# 追記対象が存在しない、または空ファイルの場合は
			# 新しいTSVとしてヘッダーを書き出す。
			should_write_header = not no_header and (not append or not output_has_content)
			
			if should_write_header:
				file_writer.writeheader()
			
			file_writer.writerows(formatted_records)
	
	def export_json(
			self,
			records: list[DriveItem],
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
			records_to_write = self._load_json_array(output)
			records_to_write.extend(clean_records)
		
		file_json_data = json.dumps(
			records_to_write,
			ensure_ascii=False,
			indent=2,
		)
		
		with open(output, 'w', encoding='utf-8') as file:
			file.write(file_json_data + '\n')
	
	def export_describe(
			self,
			record: DriveItem,
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
					records_to_write = self._load_json_array(output) + [clean_record]
			
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
		stdout_str = self._formatter.format_describe_record(clean_record, colorize=sys.stdout.isatty())
		sys.stdout.write(stdout_str + '\n')
		
		if not output:
			return
		
		# ファイル出力
		file_str = self._formatter.format_describe_record(clean_record, colorize=False)
		
		if append and output.exists() and output.stat().st_size > 0:
			file_str = '\n' + file_str
		
		write_mode = 'a' if append else 'w'
		
		with open(output, write_mode, encoding='utf-8') as file:
			file.write(file_str + '\n')
	
	@staticmethod
	def _load_json_array(path: Path) -> list[dict[str, Any]]:
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

# === Future Location: gdls.py ===

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
			pbar = self.pbar(event)
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
						f"descendants={self.subtask_trigger.steps:,}", refresh=False,
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
			output: str | None = None,
			output_format: str = 'tsv',
			no_header: bool = False,
			append_mode: bool = False,
			quiet: bool = False,
			sort_arg: str | None = None,
	) -> None:
		"""指定されたIDのコンテンツを取得し、指定形式で出力する。
		
		Args:
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
		if append_mode and not output:
			raise ValueError("append=True cannot be specified when output is None.")
		
		if no_header and output_format == 'json':
			raise ValueError("no_header=True cannot be specified when output_format='json'.")
		
		if no_header and describe_mode:
			raise ValueError("no_header=True cannot be specified when describe_mode=True.")
		
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
		)
		
		if sort_arg and not is_single_item_mode:
			sort_records(records_to_output, sort_arg)
		
		self._export_records(
			records_to_output=records_to_output,
			output_fields=output_fields,
			describe_mode=describe_mode,
			output_format=output_format,
			output_path=output_path,
			append_mode=append_mode,
			no_header=no_header,
		)
		
		logger.info(f"Items: {len(records_to_output)}")
		if output_path:
			logger.info(f"Output to: {output_path}")
	
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
			if records_to_output:
				self._exporter.export_describe(
					records_to_output[0],
					output_path,
					use_json=(output_format == 'json'),
					append=append_mode,
				)
		
		elif output_format == 'json':
			self._exporter.export_json(
				records=records_to_output,
				output=output_path,
				append=append_mode,
			)
		
		else:
			self._exporter.export_tsv(
				records=records_to_output,
				fields=output_fields,
				output=output_path,
				append=append_mode,
				no_header=no_header,
			)

# === utils.py ===

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

# === Future Location: cli.py ===

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
	parser.add_argument('-a', '--append', action='store_true',
							  help="Append to existing output file")
	
	# 出力形式
	parser.add_argument('-j', '--json', action='store_true',
							  help="Output in JSON format (instead of TSV)")
	
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
	
	if args.no_header and args.json:
		raise ValueError("The '--no-header' option can only be used with TSV output.")
	
	if args.describe and args.no_header:
		raise ValueError("The '--no-header' option cannot be used with '--describe'.")
	
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
	
	output_format = 'json' if args.json else 'tsv'
	
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
	except Exception:
		logging.error(f"Failed to retrieve Google Drive data.")
		logging.error(
			"Detailed exception information:",
			exc_info=True,
		)
		return 1
	
	return 0

if __name__ == '__main__':
	sys.exit(main())
