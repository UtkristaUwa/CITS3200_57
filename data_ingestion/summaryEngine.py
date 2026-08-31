# TenderAI Summary Engine 
# Ver. 0.2.0

"""
Inputs:
    A directory of .txt files for one tender - the scraped landing page
    plus any attached documents, all as plain text, no naming convention,
    no structured dict alongside them. Every file in the directory gets
    the same relevance check - there's no reliable way to tell "this is
    the landing page" from "this is an attachment" up front, and a good
    relevance check should recognise the landing page as relevant anyway.
Outputs:
    HEADLINE:      ~20 max headline for the tender (to go on the homepage like we discussed)
    DESCRIPTION:   2-4 paragraphs to help the user decide if the tender is worth their time
Setup:
    Install Required:   pip install pydantic tenacity google-genai
    set API Key:        export GEMINI_API_KEY="key goes here"
"""

import os
import logging
import subprocess

from google import genai
from google.genai import types
from google.genai import errors as genai_errors
from pydantic import BaseModel, Field

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

# Ignore automation warnings from SDK
# TODO: fix bcus we probably shouldnt be pulling warnings like this, but we minimise API calls this way so idk
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

# Read API key from env and set a model to prompt
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
# Internet said this was a good model for fast text processing, other option was 3.1 pro
MODEL = "gemini-3.6-flash"

# ===
# Output Schema
# TODO: Get feedback
# =========

class TenderSummary(BaseModel):
    """
    Whacky Fun 'prompt engineering' shenanigans
    Set it up this way so gemini returns a JSON object that matches this schema.
    """

    headline: str = Field(
        description=(
            "One sentence, max ~20 words. What is being procured and by whom. "
            "No agency boilerplate, no dates, no 'this is a notice that...' framing. "
            "e.g. 'AAD seeks supplier to design, build and deliver a new Antarctic "
            "fuel storage system.'"
        )
    )
    description: str = Field(
        description=(
            "2-4 short paragraphs for a client deciding whether this tender is "
            "worth pursuing. Cover: what's actually being procured and the scope "
            "of work; who is responsible for what (what's in scope vs explicitly "
            "excluded); contract value/term if stated; key dates (publish, close, "
            "delivery timeframes); and anything unusual (e.g. pre-release notice "
            "vs live tender, restrictions on contacting the agency). Do not pad "
            "with generic procurement language. If a section (e.g. value) is not "
            "stated in the source material, omit it rather than guessing."
        )
    )

# ===
# Document Triage & Relevance Filtering
# =========

class DocumentRelevance(BaseModel):
    relevant: bool = Field(
        description=(
            "True if this document contains information relevant to understanding "
            "the tender, extracting key metadata (title, agency, dates, contacts, "
            "reference IDs, URLs), scope of work, contract value/term, deliverables, "
            "mandatory criteria, evaluation methodology, addenda, or submission instructions. "
            "False if it is pure boilerplate (standard unamended contract templates), "
            "blank response forms, or low-level engineering drawings / raw CAD / spec sheets "
            "with no administrative or bid-evaluation value."
        )
    )
    reason: Optional[str] = Field(
        default=None,
        description="Brief 1-sentence reason for keeping or dropping the document."
    )


DOC_TRIAGE_SYSTEM_INSTRUCTION = """You are triaging documents from an Australian government tender package.
Your job is to decide whether this document contains useful content for understanding the tender, extracting key metadata (agency, dates, contact info, reference IDs, value, location, etc.), or evaluating scope and requirements.

Mark relevant=True for:
- Tender landing pages and overview notices (always relevant)
- Statement of Requirements / Scope of Works / Approach to Market
- Addenda, clarifications, and Q&A documents
- Evaluation criteria, conditions of participation, specifications with substantive scope

Mark relevant=False for:
- Blank response form templates
- Standard legal contract boilerplate with no custom terms (e.g. generic Commonwealth Contracting Suite terms)
- Pure technical drawing tables, part-number lists, or low-level CAD schedules with no high-level scope context
"""


def is_document_relevant(path: str) -> bool:
    """Check if a document is relevant to the tender extraction and summary process."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    # Empty or near-empty documents are not relevant
    if not text.strip():
        return False

    response = client.models.generate_content(
        model=MODEL,
        contents=f"Filename: {os.path.basename(path)}\n\n{text[:15000]}",  # first 15k chars usually suffice for triage
        config=types.GenerateContentConfig(
            system_instruction=DOC_TRIAGE_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=DocumentRelevance,
            temperature=0.1,
        ),
    )
    decision: DocumentRelevance = response.parsed
    return decision.relevant


def iter_tender_documents(directory: str):
    """Yield paths to every .txt document in a tender's directory."""
    for name in sorted(os.listdir(directory)):
        if name.lower().endswith(".txt"):
            yield os.path.join(directory, name)


def list_tender_documents(directory: str) -> list[dict]:
    """
    Reads every .txt in directory into {file_name, extracted_text} - this
    is the raw per-document list for the DB record.
    """
    documents = []
    for path in iter_tender_documents(directory):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        documents.append({
            "file_name": os.path.basename(path),
            "extracted_text": text,
        })
    return documents


def gather_relevant_documents(documents_dir: str) -> list[dict]:
    """
    1. Triages every .txt in documents_dir to drop useless files (blank templates,
       pure CAD tables, boilerplate clauses).
    2. Keeps the raw text of all relevant documents without lossy compression.
    """
    doc_paths = list(iter_tender_documents(documents_dir))
    if not doc_paths:
        return []

    # If there is only one document (e.g. the scraped tender landing page), it is inherently relevant
    if len(doc_paths) == 1:
        with open(doc_paths[0], "r", encoding="utf-8", errors="replace") as f:
            return [{"file_name": os.path.basename(doc_paths[0]), "raw_text": f.read()}]

    relevant_docs = []
    for path in doc_paths:
        try:
            if is_document_relevant(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    relevant_docs.append({
                        "file_name": os.path.basename(path),
                        "raw_text": f.read(),
                    })
        except Exception as e:
            # One problematic document shouldn't kill the whole tender processing
            print(f"  [skipped {os.path.basename(path)} during triage: {e}]")
            continue

    return relevant_docs


def build_tender_context(relevant_documents: list[dict]) -> str:
    """
    Combines the raw text of all relevant documents into a structured context block.
    """
    if not relevant_documents:
        return "(no relevant source documents found)"

    parts = []
    for doc in relevant_documents:
        parts.append(f"=== DOCUMENT: {doc['file_name']} ===\n{doc['raw_text']}")
    return "\n\n".join(parts)


# ===
# Prompt Construction
# =========

SYSTEM_INSTRUCTION = """You are a procurement analyst summarising Australian
government tender notices for a business development team
deciding which tenders to pursue.

Rules:
- Base your summary ONLY on the raw source documents provided. Never invent
  contract values, dates, or scope details that aren't present.
- Government tender notices are full of repeated legal boilerplate.
  Do not summarise the boilerplate itself — extract the substance underneath it.
- Focus on what's actually being procured, scope of work, key dates, eligibility,
  mandatory requirements, contract value/term, and anything unusual.
- If two documents repeat the same information, say it once.
- Write for someone skimming a list of tenders. Be concrete: names, numbers,
  quantities, locations.
"""


def build_prompt(raw_context: str | None) -> str:
    """
    raw_context: combined raw text from the kept relevant documents.
    """
    if not raw_context or not raw_context.strip():
        return "## Tender Source Documents\n\n(no relevant documents found)"

    return f"## Tender Source Documents\n\n{raw_context}"


# ===
# API Call
# =========

@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    reraise=True,
)
def summarise_tender(raw_context: str | None) -> TenderSummary:
    prompt = build_prompt(raw_context)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderSummary,
            temperature=0.2,
        ),
    )

    return response.parsed


def summarise_tender_full(documents_dir: str) -> TenderSummary:
    """
    Full process for a tender's directory:
    1. Filter out irrelevant files.
    2. Summarise directly from the raw text of the remaining relevant documents.
    """
    relevant_docs = gather_relevant_documents(documents_dir)
    raw_context = build_tender_context(relevant_docs)
    return summarise_tender(raw_context)


# Dummy hardcoded run to test output with the given prompt if ran on its own
if __name__ == "__main__":
    summary = summarise_tender_full("tenders_data/26-0084")
    print("HEADLINE:\n", summary.headline)
    print("\nDESCRIPTION:\n", summary.description)