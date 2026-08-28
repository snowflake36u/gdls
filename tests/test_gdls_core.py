from argparse import Namespace
import importlib
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from gdls import gdls as gdls_entry
from gdls.cli import parse_fields_arg, validate_arguments
from gdls.exceptions import CredentialFileNotFoundError, GdlsValueError
from gdls.gdls import GdlsController, extract_drive_id, sort_records
from gdls.models import DriveItem
from gdls.repository import DriveRepository

@pytest.mark.parametrize(
	("value", "expected"),
	[
		("/", "root"),
		("root", "root"),
		("https://drive.google.com/drive/folders/ABC123xyz", "ABC123xyz"),
		("https://drive.google.com/file/d/FILE_ID/view?usp=sharing", "FILE_ID"),
		("1AbC_123-xyz", "1AbC_123-xyz"),
	],
)
def test_extract_drive_id_handles_root_aliases_and_urls(value, expected):
	assert extract_drive_id(value) == expected

def test_parse_fields_arg_strips_whitespace_and_ignores_empty_values():
	assert parse_fields_arg(" id, name , size , , modifiedTime ") == [
		"id",
		"name",
		"size",
		"modifiedTime",
	]
	assert parse_fields_arg(" , , ") is None

def test_validate_arguments_rejects_invalid_option_combinations():
	append_args = Namespace(
		append=True,
		output=None,
		no_header=False,
		format="tsv",
		output_format=None,
		describe=False,
		item=False,
		recursive=False,
	)
	with pytest.raises(ValueError, match="--append"):
		validate_arguments(append_args)
	
	json_args = Namespace(
		append=False,
		output=None,
		no_header=True,
		format="json",
		output_format="json",
		describe=False,
		item=False,
		recursive=False,
	)
	with pytest.raises(ValueError, match="--no-header"):
		validate_arguments(json_args)
	
	depth_args = Namespace(
		append=False,
		output="out.tsv",
		no_header=False,
		format="tsv",
		output_format=None,
		describe=False,
		item=False,
		recursive=False,
		depth=2,
	)
	with pytest.raises(ValueError, match="--depth"):
		validate_arguments(depth_args)
	
	item_conflict = Namespace(
		append=False,
		output=None,
		no_header=False,
		format="tsv",
		output_format=None,
		describe=False,
		item=True,
		recursive=True,
		depth=None,
	)
	with pytest.raises(ValueError, match="--item.*--recursive|--recursive.*--item"):
		validate_arguments(item_conflict)

def test_package_exposes_importable_gdls_function(monkeypatch):
	captured = { }
	gdls_module = importlib.import_module("gdls.gdls")
	
	class DummyController:
		def __init__(self, repository):
			captured["repository"] = repository
		
		def execute(self, **kwargs):
			captured.update(kwargs)
			return ["returned-row"]
	
	monkeypatch.setattr(gdls_module, "get_drive_service", lambda **kwargs: object())
	monkeypatch.setattr(gdls_module, "DriveRepository", lambda service: service)
	monkeypatch.setattr(gdls_module, "GdlsController", DummyController)
	
	result = gdls_entry(
		"https://drive.google.com/drive/folders/ABC123xyz",
		sort="name",
		output="out.tsv",
		logger=logging.getLogger("gdls-test"),
		color=True,
	)
	assert result == ["returned-row"]
	assert captured["target_id"] == "ABC123xyz"
	assert captured["sort_arg"] == "name"

def test_custom_exceptions_are_structured_and_cli_friendly():
	exc = CredentialFileNotFoundError(
		"missing/client_secret.json",
		default_location="C:/Users/test/AppData/Local/SnowyTools/gdls/client_secret.json",
		env_var="GDLS_CLIENT_SECRET_FILE",
	)
	assert isinstance(exc, FileNotFoundError)
	assert isinstance(exc, GdlsValueError) is False
	assert exc.details["default_location"].endswith("client_secret.json")
	formatted = exc.format_for_cli()
	assert "Credential file not found" in formatted
	assert "Hint:" in formatted
	assert "--client-secret" in formatted
	assert "GDLS_CLIENT_SECRET_FILE" in formatted

def test_gdls_wraps_missing_client_secret_as_structured_exception(monkeypatch):
	monkeypatch.setenv("GDLS_CLIENT_SECRET_FILE", "/tmp/client_secret.json")
	gdls_module = importlib.import_module("gdls.gdls")
	
	def raise_missing(*args, **kwargs):
		raise FileNotFoundError("missing")
	
	monkeypatch.setattr(gdls_module, "get_drive_service", raise_missing)
	with pytest.raises(CredentialFileNotFoundError) as exc_info:
		gdls_entry(
			"https://drive.google.com/drive/folders/ABC123xyz",
			logger=logging.getLogger("gdls-test"),
		)
	assert exc_info.value.details["env_var"] == "GDLS_CLIENT_SECRET_FILE"


def test_validate_arguments_raises_custom_value_error_for_invalid_usage():
	append_args = Namespace(
		append=True,
		output=None,
		no_header=False,
		format="tsv",
		output_format=None,
		describe=False,
		item=False,
		recursive=False,
	)
	with pytest.raises(GdlsValueError, match="--append"):
		validate_arguments(append_args)


def test_drive_repository_retries_transient_http_errors_and_succeeds(monkeypatch):
	request = Mock()
	request.execute.side_effect = [
		HttpError(resp=SimpleNamespace(status=500, reason="Temporary failure"), content=b"temporary failure"),
		HttpError(resp=SimpleNamespace(status=429, reason="Rate limit"), content=b"rate limited"),
		{ "id": "abc", "name": "ok" },
	]
	monkeypatch.setattr("gdls.repository.time.sleep", lambda *_args, **_kwargs: None)
	
	repo = DriveRepository(service=Mock(), max_retries=3, retry_delay=0.01)
	assert repo._execute_with_retry(request) == { "id": "abc", "name": "ok" }
	assert request.execute.call_count == 3

def test_drive_item_helpers_and_record_sorts():
	folder = DriveItem({ "mimeType": "application/vnd.google-apps.folder", "name": "Folder" })
	file_item = DriveItem({ "mimeType": "text/plain", "name": "doc.txt" })
	
	assert folder.is_folder() is True
	assert folder.is_shortcut() is False
	assert file_item.is_folder() is False
	
	records = [
		DriveItem({ "name": "alpha", "size": 10 }),
		DriveItem({ "name": "beta", "size": 100 }),
		DriveItem({ "name": "gamma", "size": 30 }),
	]
	
	sort_records(records, "size desc, name")
	assert [record.get("name") for record in records] == ["beta", "gamma", "alpha"]

def test_controller_execute_fetches_and_exports_sorted_records():
	class DummyScanner:
		def build_record(self, item, output_fields, relative_path, depth):
			return DriveItem(
				{
					"id": item["id"],
					"name": item["name"],
					"size": item["size"],
					"mimeType": item["mimeType"],
					"relativePath": relative_path,
					"depth": depth,
				}
			)
	
	class DummyRepository:
		def __init__(self):
			self.calls = []
		
		def fetch_children(self, folder_id, api_fields, include_trashed=False):
			self.calls.append((folder_id, api_fields, include_trashed))
			return [
				DriveItem({ "id": "b", "name": "beta", "size": 200, "mimeType": "application/vnd.google-apps.folder" }),
				DriveItem({ "id": "a", "name": "alpha", "size": 50, "mimeType": "text/plain" }),
			]
	
	class DummyExporter:
		def __init__(self):
			self.exported = []
		
		def export(self, **kwargs):
			self.exported.append(kwargs)
	
	repo = DummyRepository()
	exporter = DummyExporter()
	controller = GdlsController(repo, exporter=exporter, scanner=DummyScanner())
	
	controller.execute(
		target_id="folder-1",
		fields=["name", "size"],
		recursive_mode=False,
		stdout_format="tsv",
		output_format="tsv",
		sort_arg="size desc",
	)
	
	assert repo.calls[0][0] == "folder-1"
	assert repo.calls[0][2] is False
	assert { "name", "size" }.issubset(set(repo.calls[0][1]))
	assert exporter.exported[0]["records"][0].get("name") == "beta"
	assert exporter.exported[0]["records"][1].get("name") == "alpha"
