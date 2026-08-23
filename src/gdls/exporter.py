import csv
import json
import shutil
import sys

import unicodedata
from pathlib import Path
from typing import Any, TextIO

from .models import DriveItem

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
	"""取得・構築されたレコードを各種形式（TSV, CSV, JSON, 詳細形式）で出力するクラス。"""
	
	def __init__(self, formatter: OutputFormatter | None = None) -> None:
		"""出力に利用する整形ロジックを初期化する。

		Args:
			formatter: 文字列整形や色付けを担うユーティリティ。
		"""
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
	
	def export(
			self,
			records: list[DriveItem],
			fields: list[str],
			stdout_format: str = 'auto',
			output: Path | None = None,
			output_format: str = 'tsv',
			append: bool = False,
			no_header: bool = False,
	) -> None:
		"""レコードを標準出力と指定ファイルへ、それぞれ指定形式で出力する。

		Args:
			records: 書き込むレコード一覧。
			fields: 出力対象のフィールド一覧。
			stdout_format: 標準出力の形式。'auto', 'grid', 'table', 'tsv', 'csv', 'json'。
			output: 出力ファイルパス。Noneの場合は標準出力のみ。
			output_format: ファイルの形式。'tsv', 'csv', 'json'。
			append: 既存ファイルへの追記を行うかどうか。
			no_header: TSV/CSV出力においてヘッダーを抑制するかどうか。
		"""
		stdout_format = self._resolve_stdout_format(stdout_format, fields)
		self._validate_format(stdout_format, output_format)
		if stdout_format == 'json' and no_header:
			raise ValueError("no_header cannot be specified when stdout_format='json'.")
		if output_format == 'json' and no_header:
			# JSONにはヘッダーという概念がないため、従来仕様と同様に拒否する。
			raise ValueError("no_header cannot be specified when output_format='json'.")
		
		# 複雑なオブジェクト型の値を文字列化してフォーマット崩れを防ぐ
		formatted_records = [
			{ k: self._formatter.format_cell_for_tsv(v) for k, v in record.items() }
			for record in records
		]
		
		self._export_to_stdout(
			stdout_format,
			records,
			formatted_records,
			fields,
			no_header,
		)
		
		if output:
			self._export_to_file(
				output_format,
				output,
				records,
				formatted_records,
				fields,
				append,
				no_header,
			)
	
	@staticmethod
	def _validate_format(stdout_format: str, output_format: str) -> None:
		if stdout_format not in { 'auto', 'grid', 'table', 'tsv', 'csv', 'json' }:
			raise ValueError(
				f"Invalid stdout_format: '{stdout_format}'. "
				"Valid formats are: auto, grid, table, tsv, csv, json."
			)
		if output_format not in { 'tsv', 'csv', 'json' }:
			raise ValueError(
				f"Invalid output_format: '{output_format}'. "
				"Valid formats are: tsv, csv, json."
			)
	
	@staticmethod
	def _resolve_stdout_format(stdout_format: str, fields: list[str]) -> str:
		if stdout_format != 'auto':
			return stdout_format
		if not sys.stdout.isatty():
			return 'tsv'
		return 'grid' if fields == ['name'] else 'table'
	
	def _export_tty_table(
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
			delimiter: str = '\t',
	) -> None:
		"""指定されたストリームに対して純粋なデリミタ区切りデータを出力する。"""
		# extrasaction='ignore' を指定し、_mimeType などの内部キーが出力されることを防ぐ
		writer = csv.DictWriter(
			stream, fieldnames=fields, delimiter=delimiter, extrasaction='ignore',
			lineterminator='\n'
		)
		if not no_header:
			writer.writeheader()
		writer.writerows(formatted_records)
	
	def _export_csv_to_file(
			self,
			output: Path,
			formatted_records: list[dict[str, str]],
			fields: list[str],
			append: bool,
			no_header: bool,
			delimiter: str = '\t',
	) -> None:
		"""ファイルに対してデリミタ区切りデータを出力する。"""
		output_exists = output.exists()
		output_has_content = output_exists and output.stat().st_size > 0
		write_mode = 'a' if append else 'w'
		
		with open(output, write_mode, encoding='utf-8', newline='') as f:
			file_writer = csv.DictWriter(
				f, fieldnames=fields, delimiter=delimiter, extrasaction='ignore'
			)
			
			# 追記対象が存在しない、または空ファイルの場合は
			# 新しいファイルとしてヘッダーを書き出す。
			should_write_header = not no_header and (not append or not output_has_content)
			
			if should_write_header:
				file_writer.writeheader()
			
			file_writer.writerows(formatted_records)
	
	def _export_to_stdout(
			self,
			stdout_format: str,
			records: list[DriveItem],
			formatted_records: list[dict[str, str]],
			fields: list[str],
			no_header: bool,
	) -> None:
		if stdout_format == 'grid':
			self.export_grid(records)
		elif stdout_format == 'table':
			self._export_tty_table(records, formatted_records, fields, no_header)
		elif stdout_format == 'tsv':
			self._export_csv_stream(
				sys.stdout, formatted_records, fields, no_header, delimiter='\t'
			)
		elif stdout_format == 'csv':
			self._export_csv_stream(
				sys.stdout, formatted_records, fields, no_header, delimiter=','
			)
		elif stdout_format == 'json':
			self._export_json_stream(records)
	
	def _export_to_file(
			self,
			output_format: str,
			output: Path,
			records: list[DriveItem],
			formatted_records: list[dict[str, str]],
			fields: list[str],
			append: bool,
			no_header: bool,
	) -> None:
		if output_format == 'json':
			self._export_json_file(output, records, append)
		elif output_format == 'csv':
			self._export_csv_to_file(
				output, formatted_records, fields, append, no_header, delimiter=','
			)
		else:
			self._export_csv_to_file(
				output, formatted_records, fields, append, no_header, delimiter='\t'
			)
	
	@staticmethod
	def _export_json_stream(records: list[DriveItem]) -> None:
		clean_records = [
			{ k: v for k, v in record.items() if not k.startswith('_') }
			for record in records
		]
		sys.stdout.write(json.dumps(clean_records, ensure_ascii=False, indent=2) + '\n')
	
	@staticmethod
	def _export_json_file(
			output: Path,
			records: list[DriveItem],
			append: bool,
	) -> None:
		clean_records = [
			{ k: v for k, v in record.items() if not k.startswith('_') }
			for record in records
		]
		records_to_write = clean_records
		if append and output.exists() and output.stat().st_size > 0:
			records_to_write = RecordExporter._load_json_array(output)
			records_to_write.extend(clean_records)
		with open(output, 'w', encoding='utf-8') as file:
			file.write(json.dumps(records_to_write, ensure_ascii=False, indent=2) + '\n')
	
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
			if append and output and output.exists() and output.stat().st_size > 0:
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
		stdout_str = self._formatter.format_describe_record(
			clean_record, colorize=sys.stdout.isatty()
		)
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
