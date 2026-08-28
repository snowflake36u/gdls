from progress_reporters import ProgressReporter, NullProgressReporter

from .models import DriveItem
from .repository import DriveRepository

# 出力フィールドと必要なAPIフィールドの対応マッピング
FIELD_API_DEPENDENCIES: dict[str, list[str]] = {
	'oldestCreatedTime': ['createdTime'],
	'latestCreatedTime': ['createdTime'],
	'oldestModifiedTime': ['modifiedTime'],
	'latestModifiedTime': ['modifiedTime'],
	'totalSize': ['size'],
	'totalQuotaBytesUsed': ['quotaBytesUsed'],
	'relativePath': ['name'],
	'relativeIdPath': ['id'],
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
			relative_id_path: str | None = None,
			depth: int = 0,
	) -> DriveItem:
		"""Google Driveアイテムのレコードを構築する。

		指定された出力フィールドに基づき、APIレスポンスから必要な属性を抽出・整形する。

		Args:
			item: APIから取得したアイテム。
			fields: 出力対象のフィールド一覧。
			relative_path: アイテムのルートからの相対パス。
			relative_id_path: アイテムのルートからの相対IDパス。
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
				case 'relativeIdPath':  # 追加
					item[field] = item.get('id', '') if relative_id_path is None else relative_id_path
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
			max_depth: int | None = None,
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
			max_depth: 探索して出力する階層の深さ上限。
		
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
				relative_path=child_name, relative_id_path=child_id, depth=child_depth,
			)
			
			if max_depth is None or child_depth <= max_depth:
				records.append(child_record)
			
			records_by_id[child_id] = child_record
			root_records.append(child_record)
			parent_ids[child_id] = root_folder_id
			
			if not child_record.is_folder():
				continue
			
			if max_depth is not None and not needs_descendant_agg and child_depth >= max_depth:
				# 深さ制限に到達しており集約も不要な場合、子孫を走査しない
				continue
			
			# === フォルダの場合は子孫要素を再帰的に探索する ===
			
			# 反復的な深さ優先探索のためのスタック構造。
			# 要素のタプル: (要素ID, 親パス, 親IDパス, 階層の深さ, 帰りがけフラグ)
			# 帰りがけフラグ(finalize)がTrueの場合、子孫の探索が完了した後の集約処理を行う。
			stack = [(child_id, child_name, child_id, child_depth, False)]
			
			while stack:
				current_id, parent_path, parent_id_path, depth, finalize = stack.pop()
				
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
					stack.append((current_id, parent_path, parent_id_path, depth, True))
				
				# 子ノードをスタックに積む。
				# reversed() を使用することで、APIから取得した順序（元の配列順）でスタックから取り出せるようにする。
				desc_depth = depth + 1
				for desc in reversed(descendants):
					desc_name = desc.get('name', '')
					desc_id = desc['id']
					
					relative_path = f'{parent_path}/{desc_name}' if parent_path else desc_name
					relative_id_path = f'{parent_id_path}/{desc_id}' if parent_id_path else desc_id
					
					desc_record = self.build_record(
						desc, output_fields,
						relative_path=relative_path,
						relative_id_path=relative_id_path,
						depth=desc_depth,
					)
					
					if max_depth is None or desc_depth <= max_depth:
						records.append(desc_record)
					
					records_by_id[desc['id']] = desc_record
					parent_ids[desc['id']] = current_id
					
					if desc_record.is_folder():
						if max_depth is None or needs_descendant_agg or desc_depth < max_depth:
							# 子アイテムがフォルダの場合は、さらにその内部を走査するためスタックに積む（行きがけ処理）
							stack.append((desc['id'], relative_path, relative_id_path, desc_depth, False))
					elif needs_descendant_agg:
						# 子アイテムがファイルで、集約が必要な場合は、自身の情報を親へ伝播させるため帰りがけ処理のみ積む
						stack.append((desc['id'], relative_path, relative_id_path, desc_depth, True))
					
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
