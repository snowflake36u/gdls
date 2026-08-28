from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from pathlib import Path
from .paths import ensure_app_data_dir, resolve_user_data_path

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
	secret_path = Path(client_secret_file) if client_secret_file \
		else resolve_user_data_path('client_secret.json')
	token_path = Path(token_file) if token_file \
		else resolve_user_data_path('token.json')
	
	ensure_app_data_dir()
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
