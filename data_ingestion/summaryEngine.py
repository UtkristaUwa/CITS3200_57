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
# Document Mapping
# =========

class DocumentFacts(BaseModel):
    relevant: bool = Field(
        description=(
            "False if this document is pure specification data, engineering "
            "drawings-as-tables, standard contract boilerplate, or anything "
            "else with no bearing on whether a client should pursue this "
            "tender. True if it contains anything about scope, value, "
            "dates, eligibility, deliverables, or requirements - this "
            "includes the tender's own landing page, which is always "
            "relevant unless it's genuinely empty of substance."
        )
    )
    facts: str = Field(
        description=(
            "If relevant=True: a compact bullet list (plain text, one fact "
            "per line) of only the tender-relevant facts in this document — "
            "no prose, no restating what's already obvious from the "
            "filename. If relevant=False: empty string."
        )
    )


DOC_MAP_SYSTEM_INSTRUCTION = """You are triaging one document from a
tender's directory of scraped .txt files. That directory has no fixed
structure - it might be just the tender's landing page on its own, or the
landing page plus a handful of attachments, or a landing page buried among
a dozen unrelated specification files. You don't know in advance which
file (if any) is the landing page, so judge each document purely on its
own content.

Your only job is to decide if THIS document contains information relevant
to a business deciding whether to bid on this tender, and if so, extract
that information as compact facts.

Most attachments in a tender package are NOT relevant to this decision:
detailed engineering specifications, part numbers, drawing sheets, standard
contract clause text, and response-form templates carry no bid/no-bid
signal. Mark these relevant=False and move on — don't try to summarise a
spec sheet's contents just because it's long.

What IS relevant: scope of work, contract value/term, key dates, eligibility
or mandatory criteria, evaluation methodology, anything unusual about the
process, or anything explicitly excluded from scope. A tender's landing
page will almost always be relevant, since it's normally where the agency,
title, status, dates and reference numbers live.
"""


def map_document(path: str) -> DocumentFacts:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    response = client.models.generate_content(
        model=MODEL,
        contents=f"Filename: {os.path.basename(path)}\n\n{text}",
        config=types.GenerateContentConfig(
            system_instruction=DOC_MAP_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=DocumentFacts,
            temperature=0.1,
        ),
    )
    return response.parsed


def iter_tender_documents(directory: str):
    """Yield paths to every .txt document in a tender's directory."""
    for name in sorted(os.listdir(directory)):
        if name.lower().endswith(".txt"):
            yield os.path.join(directory, name)


def list_tender_documents(directory: str) -> list[dict]:
    """
    Reads every .txt in directory into {file_name, extracted_text} - this
    is the raw per-document list for the DB record, separate from the
    relevance-filtered facts used to build the prompt. Original file type
    isn't recoverable from a .txt on its own, so it's just left out rather
    than guessed.
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

# ===
# Prompt Construction
# =========

# Attempts to keep gemini from halucinating and/or embellishing details
SYSTEM_INSTRUCTION = """You are a procurement analyst summarising Australian
government tender notices for a business development team
deciding which tenders to pursue.

Rules:
- Base your summary ONLY on the material provided. Never invent contract
  values, dates, or scope details that aren't present.
- Government tender notices are full of repeated legal boilerplate
  ("this is not a commitment by the Commonwealth to purchase...",
  disclaimers, contact-procedure instructions). Do not summarise the
  boilerplate itself — extract the substance underneath it.
- Attached documents using the Commonwealth Contracting Suite (CCS)
  template — recognisable by headings like "Additional Contract Terms",
  "Commonwealth Contract Terms", "CCS ATM Response Form", or a
  "Commonwealth Contracting Suite Glossary and Interpretation" section —
  contain large sections of standard clause text (IP ownership, payment
  terms, response-form submission instructions) that is near-identical
  across almost every Commonwealth tender. This is boilerplate, not
  content: ignore it entirely unless a clause has been specifically
  amended or flagged as non-standard for this tender. The material that
  matters is almost always in the "Statement of Requirement" section
  near the start of the document.
- If two documents repeat the same information, say it once. Where one
  document has more detail than another (which is typical of a landing
  page vs. an attachment), the description should be built mainly from
  whichever has the most substance.
- Write for someone skimming a list of 50 tenders, not someone who will
  read the source documents. Be concrete: names, numbers, quantities,
  locations.
"""


def build_prompt(document_facts: str | None) -> str:
    """
    document_facts: the combined, relevance-filtered facts pulled from
    every .txt in the tender's directory (landing page + attachments
    alike) - see gather_document_facts.
    """
    if not document_facts:
        return "## Tender facts\n\n(no relevant facts found in the source documents)"

    return f"## Tender facts (extracted from source documents)\n\n{document_facts}"


# ===
# API Call
# =========

@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),  # 503s etc NOT client errors
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),  # 2s, 4s, 8s, ~16s
    reraise=True,
)

def summarise_tender(document_facts: str | None) -> TenderSummary:
    prompt = build_prompt(document_facts)

    response = client.models.generate_content(
        model=MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=TenderSummary,
            temperature=0.2, # "temperature" controls the randomness of the model's output.
            # Set it low but not 0 in an attempts to prevent any hallucination, but also have the output phrased in a user-friendly manner
        ),
    )

    return response.parsed

def gather_document_facts(documents_dir: str) -> str | None:
    """
    Maps every .txt in documents_dir to compact facts and drops irrelivant
    documents (landing page included - it goes through the same check as
    everything else). Pulled out on its own so the field extractor can
    reuse the same mapped facts instead of re-running map_document
    on every document a second time.
    """
    all_facts = []
    for path in iter_tender_documents(documents_dir):
        try:
            result = map_document(path)
        except Exception as e:
            # one bad attachment (corrupt file, unsupported type) shouldn't
            # kill the whole tender's summary — log and move on
            print(f"  [skipped {os.path.basename(path)}: {e}]")
            continue
        if result.relevant and result.facts.strip():
            all_facts.append(f"### {os.path.basename(path)}\n{result.facts}")

    return "\n\n".join(all_facts) if all_facts else None


def summarise_tender_full(documents_dir: str) -> TenderSummary:
    """
    Full process for a tender's directory; maps every .txt to a compact
    list of facts and drops irrelivant ones, and only then does it
    produce the final headline + body item
    """
    document_facts = gather_document_facts(documents_dir)
    return summarise_tender(document_facts)


# Dummy hardcoded run to test output with the given prompt if ran on its own
if __name__ == "__main__":

    # Expects a directory of .txt files - landing page + any attachments, mixed together
    summary = summarise_tender_full("tenders_data/26-0084")

    print("HEADLINE:\n", summary.headline)
    print("\nDESCRIPTION:\n", summary.description)