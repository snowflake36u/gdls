import time
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from .models import DriveItem

# 5xxエラー（サーバーエラー）または429（レート制限）のみリトライ対象とする
RETRY_HTTP_CODES = {
	429, 500, 502, 503, 504,
}

class DriveRepository:
	"""Google Drive APIとの通信およびデータ取得を担当するリポジトリクラス。"""
	
	def __init__(self, service: Resource, max_retries: int = 3, retry_delay: float = 1.0) -> None:
		self._service = service
		self._max_retries = max_retries
		self._retry_delay = retry_delay
	
	@property
	def service(self) -> Resource:
		"""Google Drive APIサービスインスタンスを取得する。"""
		return self._service
	
	def _execute_with_retry(self, request):
		"""APIリクエストを実行し、一時的なエラーが発生した場合は指数バックオフでリトライする。

		Args:
			request: executeメソッドを持つGoogle APIリクエストオブジェクト。

		Returns:
			APIレスポンスの実行結果。

		Raises:
			HttpError: 最大リトライ回数を超過したか、リトライ対象外のエラーが発生した場合。
			RuntimeError: 予期せぬ理由によりリトライループを抜け出した場合。
		"""
		delay = self._retry_delay
		for attempt in range(self._max_retries + 1):
			try:
				return request.execute()
			except HttpError as error:
				# 5xxエラー（サーバーエラー）または429（レート制限）のみリトライ対象とする
				if error.resp.status not in RETRY_HTTP_CODES or attempt >= self._max_retries:
					raise
			except (OSError, ConnectionError):
				# 一時的なネットワークエラーの場合もリトライ対象とする
				if attempt >= self._max_retries:
					raise
			except Exception:
				raise
			
			# リトライを行う
			time.sleep(delay)
			delay *= 2
		
		raise RuntimeError('Unreachable code reached in _execute_with_retry.')
	
	def fetch_item(self, item_id: str, api_fields: list[str]) -> DriveItem:
		"""指定されたIDの単一アイテム情報を取得する。

		Args:
			item_id: 対象のファイルまたはフォルダID。
			api_fields: 取得するAPIフィールド一覧。

		Returns:
			アイテム情報の辞書。
		"""
		request = self._service.files().get(
			fileId=item_id,
			fields=', '.join(api_fields),
			supportsAllDrives=True,
		)
		return DriveItem(self._execute_with_retry(request))
	
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
			request = self._service.files().list(
				q=query,
				spaces='drive',
				fields=fields,
				pageToken=page_token,
				includeItemsFromAllDrives=True,
				supportsAllDrives=True,
			)
			response = self._execute_with_retry(request)
			
			children.extend(map(DriveItem, response.get('files', [])))
			
			page_token = response.get('nextPageToken')
			if not page_token:
				break
		
		return children
