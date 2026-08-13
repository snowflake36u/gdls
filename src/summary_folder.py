import argparse
import csv
import re
import logging
import sys
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

# デフォルトの出力属性定義
DEFAULT_OUTPUT_HEADERS = [
	'id',
	'webViewLink',
	'name',
	'mimeType',
	'description',
	'owners',
	'modifiedTime',
	'viewedByMeTime',
	'createdTime',
	'oldestCreatedTime',
	'size',
	'quotaBytesUsed',
]

# 出力属性と必要なAPIフィールドの対応マッピング
HEADER_API_DEPENDENCIES: dict[str, list[str]] = {
	'oldestCreatedTime': ['createdTime'],
	'totalSize': ['size'],
	'totalQuotaBytesUsed': ['quotaBytesUsed'],
	'owners': ['owners'],
	'size': ['size'],
	'quotaBytesUsed': ['quotaBytesUsed'],
	'createdTime': ['createdTime'],
}

# 子孫要素の取得・集約が必要な属性一覧
DESCENDANT_AGGREGATED_HEADERS: set[str] = {
	'oldestCreatedTime',
	'totalSize',
	'totalQuotaBytesUsed',
}

def get_drive_service(client_secret_file: str | None = None, token_file: str | None = None):
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
				raise FileNotFoundError(f"Client secret file not found at: {secret_path}")
			flow = InstalledAppFlow.from_client_secrets_file(str(secret_path), SCOPES)
			# ローカルサーバーを起動して認証
			creds = flow.run_local_server(port=0)
		with open(str(token_path), 'w') as token:
			token.write(creds.to_json())
	return build('drive', 'v3', credentials=creds)

def extract_folder_id(url_or_id: str) -> str:
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

def get_required_api_fields(output_headers: list[str]) -> tuple[list[str], list[str]]:
	"""出力対象ヘッダーに必要な API フィールドの集合を決定する"""
	root_fields = { 'id', 'mimeType' }
	descendant_fields = { 'id', 'mimeType' }
	
	for header in output_headers:
		# API フィールド依存が定義されていればそれを採用し、なければ属性名をそのままAPIフィールド名として扱う
		deps = HEADER_API_DEPENDENCIES.get(header, [header])
		root_fields.update(deps)
		
		if header in DESCENDANT_AGGREGATED_HEADERS or header == 'createdTime':
			descendant_fields.update(deps)
	
	return list(root_fields), list(descendant_fields)

def fetch_children(service, folder_id: str, api_fields: list[str], include_trashed: bool = False) -> list[dict]:
	"""指定フォルダの直下の要素一覧を取得する"""
	trashed_query = "" if include_trashed else " and trashed=false"
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
			supportsAllDrives=True
		).execute()
		
		children.extend(response.get('files', []))
		page_token = response.get('nextPageToken', None)
		if not page_token:
			break
	return children

def iter_descendants(service, folder_id: str, api_fields: list[str], include_trashed: bool = False):
	"""フォルダ配下の全子孫要素をスタックを用いて反復処理で取得・yieldする"""
	stack = [folder_id]
	while stack:
		current_id = stack.pop()
		children = fetch_children(service, current_id, api_fields, include_trashed)
		for child in children:
			yield child
			if child.get('mimeType') == 'application/vnd.google-apps.folder':
				stack.append(child['id'])

def get_owner_name(item: dict) -> str:
	"""アイテムからオーナー名を取得する"""
	owners = item.get('owners', [])
	return owners[0].get('displayName', '') if owners else ''

def is_folder_item(item: dict) -> bool:
	"""アイテムがフォルダかどうかを判定する"""
	return item.get('mimeType') == 'application/vnd.google-apps.folder'

def aggregate_descendants(
		service,
		folder_id: str,
		desc_api_fields: list[str],
		include_trashed: bool = False,
		quiet: bool = False,
) -> tuple[str, int, int]:
	"""フォルダの子孫要素を集約し、最古作成日時、合計サイズ、合計quotaを返す

	Returns:
		(最古作成日時, 合計サイズ, 合計quota)
	"""
	oldest_date = ''
	total_size = 0
	total_quota = 0
	
	descendants_generator = iter_descendants(service, folder_id, desc_api_fields, include_trashed)
	descendant_iter = tqdm(
		descendants_generator, desc="Processing descendant items", unit=" item", position=1, leave=False, disable=quiet, file=sys.stderr
	)
	
	for desc in descendant_iter:
		desc_size = int(desc.get('size') or 0)
		desc_quota = int(desc.get('quotaBytesUsed') or 0)
		desc_created = desc.get('createdTime', '')
		
		total_size += desc_size
		total_quota += desc_quota
		
		if desc_created and (not oldest_date or desc_created < oldest_date):
			oldest_date = desc_created
	
	return oldest_date, total_size, total_quota

def build_item_record(
		item: dict,
		headers: list[str],
		service=None,
		desc_api_fields: list[str] | None = None,
		needs_descendant_agg: bool = False,
		include_trashed: bool = False,
		quiet: bool = False,
) -> dict:
	"""フォルダアイテムのレコードを構築する

	Args:
		item: APIから取得したアイテム
		headers: 出力対象のヘッダー一覧
		service: Google Drive APIサービス（子孫集約が必要な場合）
		desc_api_fields: 子孫要素取得に必要なAPIフィールド
		needs_descendant_agg: 子孫要素の集約が必要か
		include_trashed: ゴミ箱内のファイルも含めて集計・出力するかどうか。
		quiet: 進捗バーの出力を抑制するかどうか。

	Returns:
		構築されたレコード辞書
	"""
	owner_name = get_owner_name(item)
	created_time = item.get('createdTime', '')
	oldest_date = created_time
	
	# API準拠の生データを取得
	raw_size = int(item.get('size') or 0)
	raw_quota = int(item.get('quotaBytesUsed') or 0)
	
	# 独自集計用の初期値設定
	total_size = raw_size
	total_quota = raw_quota
	
	# フォルダで子孫集約が必要な場合、子孫要素を集約
	if is_folder_item(item) and needs_descendant_agg and service:
		desc_oldest, desc_size, desc_quota = aggregate_descendants(
			service, item['id'], desc_api_fields or [], include_trashed, quiet=quiet
		)
		if desc_oldest:
			oldest_date = desc_oldest if not created_time or desc_oldest < created_time else created_time
		total_size += desc_size
		total_quota += desc_quota
	
	# レコードを構築
	record = { }
	for header in headers:
		if header == 'owners':
			record[header] = owner_name
		elif header == 'oldestCreatedTime':
			record[header] = oldest_date
		elif header == 'totalSize':
			record[header] = total_size
		elif header == 'totalQuotaBytesUsed':
			record[header] = total_quota
		else:
			# APIが返した値をそのまま設定する
			record[header] = item.get(header, '')
	
	return record

def write_records_to_tsv(
		records: list[dict],
		headers: list[str],
		output: Path | None,
		append: bool = False
) -> None:
	"""レコードを標準出力に書き込みつつ、指定があればTSVファイルにも書き込む

	出力先が指定されていない場合は標準出力に書き出し、後続のパイプ処理などを可能にする。

	Args:
		records: 書き込むレコード一覧
		headers: ヘッダー一覧
		output: 出力ファイルパス。None の場合は標準出力のみ
		append: 追記モード（ファイル出力時のみ影響）
	"""
	# 常に標準出力に書き出す
	stdout_writer = csv.DictWriter(sys.stdout, fieldnames=headers, delimiter='\t')
	stdout_writer.writeheader()
	stdout_writer.writerows(records)
	
	# 出力ファイルが指定されている場合は、ファイルにも書き出す
	if output:
		write_mode = 'a+' if append else 'w'
		with open(str(output), write_mode, encoding='utf-8', newline='') as f:
			file_writer = csv.DictWriter(f, fieldnames=headers, delimiter='\t')
			# 追記モードでない場合のみヘッダーを書き込む
			if not append:
				file_writer.writeheader()
			file_writer.writerows(records)

def list_drive_folder(
		service,
		target_folder_id: str,
		fields: list[str] | None = None,
		include_trashed: bool = False,
		self_target: bool = False,
		output: str | None = None,
		append_mode: bool = False,
		quiet: bool = False,
) -> None:
	"""フォルダ内の情報を取得し、TSV に出力する

	指定フォルダの直下の要素をリストアップします。
	子要素がフォルダの場合、内部の子孫要素のデータを集約します。

	self_target=True の場合は、指定された ID そのものの情報のみを取得します。

	Args:
		service: Google Drive APIサービス
		target_folder_id: 対象フォルダID
		output: 出力ファイル名。None の場合は標準出力
		append_mode: 既存ファイルに追記するか
		fields: 出力対象のヘッダー（Noneの場合は自動的に判定する）
		include_trashed: ゴミ箱内のファイルも含めて集計・出力するかどうか。
		self_target: 対象ファイル・フォルダ自体の情報のみを出力するかどうか。
		quiet: 進捗バーの出力を抑制するかどうか。
	"""
	_fields = fields if fields is not None else DEFAULT_OUTPUT_HEADERS
	output_path = get_output_path(output) if output else None
	
	root_api_fields, desc_api_fields = get_required_api_fields(_fields)
	needs_descendant_agg = any(
		h in _fields for h in ('oldestCreatedTime', 'totalSize', 'totalQuotaBytesUsed')
	)
	
	logger = logging.getLogger('GoogleDriveLister')
	logger.info("Fetching data from Google Drive...")
	
	if self_target:
		# 対象アイテムそのものを1つだけ取得
		fields_str = ', '.join(root_api_fields)
		try:
			item = service.files().get(
				fileId=target_folder_id,
				fields=fields_str,
				supportsAllDrives=True
			).execute()
			root_children = [item]
		except Exception as e:
			logger.error(f"Failed to fetch target item: {e}")
			return
	else:
		# 指定フォルダの直下要素を取得
		root_children = fetch_children(service, target_folder_id, root_api_fields, include_trashed)
		
		# 直下要素が0件だった場合、対象ID自体が非フォルダである可能性があるので確認する
		if len(root_children) == 0:
			try:
				# 0件だった場合のみ、対象の MIME タイプを確認 (必要な場合のみ呼び出し)
				target_info = service.files().get(
					fileId=target_folder_id,
					fields='mimeType',
					supportsAllDrives=True
				).execute()
				
				if target_info.get('mimeType') != 'application/vnd.google-apps.folder':
					logger.warning(f"Warning: Specified ID '{target_folder_id}' is not a folder.")
					logger.warning("If you want to fetch information for this file itself, please use the '--self' option.")
			except Exception as e:
				# IDが存在しないかアクセス権がない場合などの例外ハンドリング
				logger.debug(f"Target item check skipped or failed: {e}")
	
	# アイテムを処理
	all_records = []
	root_iter = tqdm(
		root_children, desc="Processing root items", unit=" item", position=0, disable=quiet, file=sys.stderr
	)
	
	for child in root_iter:
		record = build_item_record(
			child,
			_fields,
			service=service,
			desc_api_fields=desc_api_fields,
			needs_descendant_agg=needs_descendant_agg,
			include_trashed=include_trashed,
			quiet=quiet,
		)
		all_records.append(record)
	
	# TSV 出力
	write_records_to_tsv(all_records, _fields, output_path, append_mode)
	
	logger.info(f"Items: {len(all_records)}")
	if output_path:
		logger.info(f"Output to: {output_path}")

def parse_arguments():
	"""コマンドライン引数を解析する

	Returns:
		パース済みの引数名前空間
	"""
	parser = argparse.ArgumentParser(
		description='Export Google Drive folder contents to TSV'
	)
	parser.add_argument('id', help='Google Drive folder URL or folder ID')
	parser.add_argument('--include-trashed', action='store_true',
							  help='Include trashed files in the calculation and output')
	parser.add_argument('-s', '--self', action='store_true',
							  help='Fetch and output information ONLY for the specified target file/folder itself')
	parser.add_argument('-f', '--fields', help='Attributes to export (comma separated)')
	
	parser.add_argument('--log-level', type=str, default='INFO',
							  choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
							  help='Set the logging level (default: INFO)')
	
	parser.add_argument('--client-secret', type=str, default=None,
							  help='Path to client_secret.json (overrides SNOWY_GDL_CLIENT_SECRET_FILE env var)')
	parser.add_argument('--token-file', type=str, default=None,
							  help='Path to token.json (overrides SNOWY_GDL_TOKEN_FILE env var)')
	
	parser.add_argument('-o', '--output', type=str,
							  help='Output TSV file path')
	parser.add_argument('-a', '--append', action='store_true', help='Append to existing output file')
	
	parser.add_argument('-q', '--quiet', action='store_true',
							  help='Suppress progress bar and non-error log messages')
	
	return parser.parse_args()

def parse_output_headers(fields_arg: str | None) -> list[str] | None:
	"""フィールド引数を解析して出力ヘッダーを取得する

	Args:
		fields_arg: カンマ区切りのフィールド文字列

	Returns:
		フィールド一覧、または None（デフォルトを使用する場合）
	"""
	if not fields_arg:
		return None
	
	fields_list = [f.strip() for f in fields_arg.split(',') if f.strip()]
	return fields_list if fields_list else None

def main():
	"""メイン処理

	Google Drive フォルダの内容を取得し、TSV ファイルまたは標準出力にエクスポートする
	"""
	args = parse_arguments()
	
	if args.quiet:
		# ログレベルをERROR以上に強制し、エラー出力を非表示にする
		args.log_level = 'ERROR'
	
	# コマンドライン引数に基づいてロギングを設定し、標準エラー出力に振り向ける
	numeric_level = getattr(logging, args.log_level.upper(), None)
	logging.basicConfig(level=numeric_level, format='%(message)s', stream=sys.stderr)
	
	target_id = extract_folder_id(args.id)
	if not target_id:
		logging.error("Error: Unable to extract a valid folder ID.")
		return
	
	output_headers = parse_output_headers(args.fields)
	
	try:
		service = get_drive_service(
			client_secret_file=args.client_secret,
			token_file=args.token_file
		)
	except FileNotFoundError as e:
		logging.error(f"Error: {e}")
		logging.error("Please place the client secret JSON file at the expected location or provide its path via --client-secret.")
		return
	except KeyboardInterrupt:
		logging.error("Operation cancelled by user.")
		return
	except Exception as e:
		logging.error(f"An unexpected error occurred: {e}")
		return
	
	list_drive_folder(
		service,
		target_id,
		fields=output_headers,
		include_trashed=args.include_trashed,
		self_target=args.self,
		output=args.output,
		append_mode=args.append,
		quiet=args.quiet,
	)

if __name__ == '__main__':
	main()
