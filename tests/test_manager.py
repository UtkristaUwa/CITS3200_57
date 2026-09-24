"""
manager._merge_document_records: pairing attachment_store's storage records
with the extracted text tender_processor read from each attachment's sibling
<base>.txt file, into the shape the `documents` BigQuery column needs.
"""

import manager


def test_pairs_an_attachment_with_its_extracted_text():
    attachments = [{"file_name": "Requirements.pdf", "storage_uri": "gs://b/Requirements.pdf"}]
    txt_documents = [{"file_name": "Requirements.txt", "extracted_text": "the extracted body"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged == [{
        "file_name": "Requirements.pdf",
        "storage_uri": "gs://b/Requirements.pdf",
        "extracted_text": "the extracted body",
    }]


def test_a_genuine_txt_attachment_is_paired_with_its_own_content():
    # A real portal-provided .txt file: tender_processor reads every .txt
    # in the folder as its own "extracted text", including this one.
    attachments = [{"file_name": "Addendum 1.txt", "storage_uri": "gs://b/Addendum 1.txt"}]
    txt_documents = [{"file_name": "Addendum 1.txt", "extracted_text": "a real attachment"}]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert merged[0]["extracted_text"] == "a real attachment"


def test_an_attachment_with_no_extraction_gets_no_text_rather_than_failing():
    attachments = [{"file_name": "scan.pdf", "storage_uri": "gs://b/scan.pdf"}]

    merged = manager._merge_document_records(attachments, [])

    assert merged[0]["extracted_text"] is None


def test_the_tenders_own_page_text_is_dropped_not_carried_into_documents():
    # <REF>.txt has no matching attachment -- it must not end up in
    # `documents` at all, real or otherwise.
    attachments = [{"file_name": "Requirements.pdf", "storage_uri": "gs://b/Requirements.pdf"}]
    txt_documents = [
        {"file_name": "Requirements.txt", "extracted_text": "body"},
        {"file_name": "GO8232.txt", "extracted_text": "page text"},
    ]

    merged = manager._merge_document_records(attachments, txt_documents)

    assert [d["file_name"] for d in merged] == ["Requirements.pdf"]


def test_no_attachments_means_no_documents():
    txt_documents = [{"file_name": "GO8232.txt", "extracted_text": "page text"}]

    assert manager._merge_document_records([], txt_documents) == []
