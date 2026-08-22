from googleapiclient.discovery import Resource

from .models import DriveItem

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
