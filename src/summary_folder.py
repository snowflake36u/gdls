import os
import csv
import re
import argparse
from pathlib import Path
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from tqdm import tqdm
from config import ensure_directories, get_output_path, get_client_secret_file, get_token_file

# スコープの設定（読み取り専用）
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# 動作設定
# 指定フォルダの直下の要素のみをリストアップします。
# 子要素がフォルダの場合、内部の子孫要素のデータを集約して合計ファイルサイズや最古作成日時を計算します。

# アイテム情報の取得時、APIに問い合わせるフィールド（APIの属性名）
API_FIELDS = [
	'id',
	'webViewLink',
	'name',
	'mimeType',
	'description',
	'owners',
	'modifiedTime',
	'viewedByMeTime',
	'createdTime',
	'size',
	'quotaBytesUsed'
]

# 出力する列の定義（APIの属性名をそのまま使用し、独自の属性を追加）
OUTPUT_HEADERS = [
	'id',
	'webViewLink',
	'name',
	'mimeType',
	'description',
	'owners',
	'modifiedTime',
	'viewedByMeTime',
	'createdTime',
	'oldestDescendantCreationTime',  # 子孫アイテムの最古作成日 (独自属性)
	'size',
	'quotaBytesUsed'
]

# API フィールド定義の取得
def get_api_fields():
	"""APIから取得する必要なフィールドのリストを生成"""
	return API_FIELDS

def get_drive_service(client_secret_file=None, token_file=None):
	"""Google Drive API のサービスを構築・認証する
	
	Args:
		client_secret_file: クライアントシークレットファイルのパス
		token_file: トークンファイルのパス
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
				raise FileNotFoundError(
					f"Client Secret file not found. Please place client_secret.json at: {secret_path}. See SETUP.md for instructions on how to obtain it."
				)
			flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
			# ローカルサーバーを起動して認証
			creds = flow.run_local_server(port=0)
		with open(str(token_path), 'w') as token:
			token.write(creds.to_json())
	return build('drive', 'v3', credentials=creds)

def extract_folder_id(url_or_id):
	"""URL または ID 文字列からフォルダ ID のみを抽出する"""
	# URL形式のチェック (例: https://drive.google.com/drive/folders/XXXXX)
	match = re.search(r'folders/([a-zA-Z0-9_-]+)', url_or_id)
	if match:
		return match.group(1)
	# URL パラメータ形式のチェック (例: id=XXXXX)
	match = re.search(r'id=([a-zA-Z0-9_-]+)', url_or_id)
	if match:
		return match.group(1)
	# そのまま ID として返す
	return url_or_id

def export_folder_data(service, target_folder_id, output_filename='drive_contents.tsv', append_mode=False):
	"""フォルダ内の情報を取得し、TSV に出力する

	指定フォルダの直下の要素のみをリストアップします。
	子要素がフォルダの場合、内部の子孫要素のデータを集約します。
	"""
	output_path = get_output_path(output_filename)
	all_records = []
	
	def fetch_children(folder_id):
		"""指定フォルダの直下の要素一覧を取得する"""
		query = f"'{folder_id}' in parents and trashed=false"
		api_fields = get_api_fields()
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
				supportsAllDrives=True
			).execute()
			
			children.extend(response.get('files', []))
			page_token = response.get('nextPageToken', None)
			if not page_token:
				break
		return children
	
	def iter_descendants(folder_id):
		"""フォルダ配下の全子孫要素を再帰的に取得・yieldするジェネレータ"""
		children = fetch_children(folder_id)
		for child in children:
			yield child
			if child.get('mimeType') == 'application/vnd.google-apps.folder':
				yield from iter_descendants(child['id'])
	
	print("Fetching data from Google Drive...")
	
	# 指定フォルダの直下要素を取得
	root_children = fetch_children(target_folder_id)
	
	# 1段階目: 直下ファイルアイテムのイテレーション (position=0)
	root_iter = tqdm(root_children, desc="Processing root items", unit=" item", position=0)
	
	for child in root_iter:
		is_folder = child.get('mimeType') == 'application/vnd.google-apps.folder'
		
		# オーナー情報の取得
		owners = child.get('owners', [])
		owner_name = owners[0].get('displayName', '') if owners else ''
		
		created_time = child.get('createdTime', '')
		child_oldest_date = created_time
		
		size = int(child.get('size', 0))
		quota = int(child.get('quotaBytesUsed', 0))
		
		# 2段階目: 階層をまたぐ子孫ファイルアイテムのイテレーション (position=1)
		if is_folder:
			descendants_generator = iter_descendants(child['id'])
		else:
			descendants_generator = []
		
		descendant_iter = tqdm(descendants_generator, desc="Processing descendant items", unit=" item", position=1, leave=False)
		
		for desc in descendant_iter:
			desc_size = int(desc.get('size', 0))
			desc_quota = int(desc.get('quotaBytesUsed', 0))
			desc_created = desc.get('createdTime', '')
			
			size += desc_size
			quota += desc_quota
			
			if desc_created and (not child_oldest_date or desc_created < child_oldest_date):
				child_oldest_date = desc_created
		
		# レコードの構築
		record = { }
		for header in OUTPUT_HEADERS:
			if header == 'owners':
				record[header] = owner_name
			elif header == 'oldestDescendantCreationTime':
				record[header] = child_oldest_date
			elif header == 'size':
				record[header] = size
			elif header == 'quotaBytesUsed':
				record[header] = quota
			else:
				record[header] = child.get(header, '')
		
		all_records.append(record)
	
	# TSV 出力
	headers = OUTPUT_HEADERS
	
	write_mode = 'a+' if append_mode else 'w'
	
	with open(str(output_path), write_mode, encoding='utf-8', newline='') as f:
		writer = csv.DictWriter(f, fieldnames=headers, delimiter='\t')
		if not append_mode:
			writer.writeheader()
		writer.writerows(all_records)
	
	print(f"Export completed!")
	print(f"Items: {len(all_records)}")
	print(f"Output: {output_path}")

def main():
	# Google Drive フォルダ URL または ID の入力受付
	parser = argparse.ArgumentParser(
		description='Export Google Drive folder contents to TSV'
	)
	parser.add_argument('id', help='Google Drive folder URL or folder ID')
	parser.add_argument('-a', '--append', action='store_true', help='Append to existing output file')
	parser.add_argument('--client-secret', type=str, default=None,
		help='Path to client_secret.json (overrides SNOWY_GDL_CLIENT_SECRET_FILE env var)')
	parser.add_argument('--token-file', type=str, default=None,
		help='Path to token.json (overrides SNOWY_GDL_TOKEN_FILE env var)')
	
	args = parser.parse_args()
	
	target_id = extract_folder_id(args.id)
	append_mode = args.append
	
	if not target_id:
		print("Error: Unable to extract valid folder ID.")
	else:
		service = get_drive_service(
			client_secret_file=args.client_secret,
			token_file=args.token_file
		)
		export_folder_data(service, target_id, output_filename='drive_contents.tsv', append_mode=append_mode)

if __name__ == '__main__':
	main()