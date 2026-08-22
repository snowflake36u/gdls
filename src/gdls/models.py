from typing import Any

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
