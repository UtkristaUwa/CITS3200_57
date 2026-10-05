import importlib
import sys

import pytest
from google import genai


@pytest.fixture
def processor(monkeypatch):
    monkeypatch.setattr(genai, "Client", lambda **_kwargs: object())
    module_name = "processing.tender_processor"
    previous = sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "is_document_relevant", lambda path: True)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)
        if previous is not None:
            sys.modules[module_name] = previous


def _write(folder, name, text):
    (folder / name).write_text(text, encoding="utf-8")


def test_page_text_comes_first_ahead_of_attachments(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "contact_name: Naomi Picker")
    _write(tmp_path, "a.pdf.txt", "attachment a")
    _write(tmp_path, "b.docx.txt", "attachment b")
    docs = processor.gather_relevant_documents(str(tmp_path))
    assert [d["file_name"] for d in docs] == ["__tender__ATM1.txt", "a.pdf.txt", "b.docx.txt"]
    assert docs[0]["raw_text"] == "contact_name: Naomi Picker"


def test_page_text_is_included_with_a_single_attachment(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "page")
    _write(tmp_path, "only.pdf.txt", "attachment")
    docs = processor.gather_relevant_documents(str(tmp_path))
    assert [d["file_name"] for d in docs] == ["__tender__ATM1.txt", "only.pdf.txt"]


def test_page_text_alone_is_returned_once_not_twice(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "page")
    docs = processor.gather_relevant_documents(str(tmp_path))
    assert [d["file_name"] for d in docs] == ["__tender__ATM1.txt"]


def test_attachments_without_a_page_file_are_unchanged(processor, tmp_path):
    _write(tmp_path, "a.pdf.txt", "attachment a")
    _write(tmp_path, "b.pdf.txt", "attachment b")
    docs = processor.gather_relevant_documents(str(tmp_path))
    assert [d["file_name"] for d in docs] == ["a.pdf.txt", "b.pdf.txt"]


def test_a_blank_page_file_is_ignored(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "   \n")
    _write(tmp_path, "a.pdf.txt", "attachment a")
    _write(tmp_path, "b.pdf.txt", "attachment b")
    docs = processor.gather_relevant_documents(str(tmp_path))
    assert [d["file_name"] for d in docs] == ["a.pdf.txt", "b.pdf.txt"]


def test_empty_folder_gives_no_documents(processor, tmp_path):
    assert processor.gather_relevant_documents(str(tmp_path)) == []


def test_the_stored_document_list_still_excludes_the_page_file(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "page")
    _write(tmp_path, "a.pdf.txt", "attachment a")
    names = [d["file_name"] for d in processor.list_tender_documents(str(tmp_path))]
    assert names == ["a.pdf.txt"]


def test_page_text_survives_the_context_cap(processor, tmp_path):
    _write(tmp_path, "__tender__ATM1.txt", "PAGE")
    _write(tmp_path, "a.pdf.txt", "x" * 100)
    _write(tmp_path, "b.pdf.txt", "y" * 100)
    context = processor.build_tender_context(
        processor.gather_relevant_documents(str(tmp_path)))
    assert context.index("PAGE") < context.index("x" * 10)
