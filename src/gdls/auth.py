# auth.py
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from pathlib import Path

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

def get_drive_service(
		client_secret_file: str | Path,
		token_file: str | Path,
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
	secret_path = Path(client_secret_file)
	token_path = Path(token_file)
	
	creds = None
	
	if token_path.exists():
		creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
	
	if not creds or not creds.valid:
		if creds and creds.expired and creds.refresh_token:
			creds.refresh(Request())
		else:
			if not secret_path.exists():
				raise FileNotFoundError(str(secret_path))
			flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
			creds = flow.run_local_server(port=0)
		with open(token_path, 'w', encoding='utf-8') as token:
			token.write(creds.to_json())
	
	return build('drive', 'v3', credentials=creds)
