# TenderAI Summary Engine 
# Ver. 0.1.0

"""
Inputs:
    Takes any scraped input from some tender <--- Tenitatively hardcoded with the data from a single AUSTENDER Tender
    Exact nature and format of input TBD
Outputs:
    HEADLINE:      ~20 max headline for the tender (to go on the homepage like we discussed)
    DESCRIPTION:   2-4 paragraphs to help the user decide if the tender is worth their time
Setup:
    Install SDK:   pip install google-genai
    set API Key:   export GEMINI_API_KEY="key goes here"
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
            "dates, eligibility, deliverables, or requirements."
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


DOC_MAP_SYSTEM_INSTRUCTION = """You are triaging one document from a larger
set of attached tender documents. Your only job is to decide if it contains
information relevant to a business deciding whether to bid on this tender,
and if so, extract that information as compact facts.

Most attachments in a tender package are NOT relevant to this decision:
detailed engineering specifications, part numbers, drawing sheets, standard
contract clause text, and response-form templates carry no bid/no-bid
signal. Mark these relevant=False and move on — don't try to summarise a
spec sheet's contents just because it's long.

What IS relevant: scope of work, contract value/term, key dates, eligibility
or mandatory criteria, evaluation methodology, anything unusual about the
process, or anything explicitly excluded from scope.
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
- If the notice and the attached document repeat the same information,
  say it once. If the attached document has more detail than the notice
  (which is typical), the description should be built mainly from the
  attached document.
- Write for someone skimming a list of 50 tenders, not someone who will
  read the source documents. Be concrete: names, numbers, quantities,
  locations.
"""


def build_prompt(notice_fields: dict, attached_documents: str | None = None) -> str:
    """
    notice_fields: whatever key/value pairs we have after scraping the tender's main listing page
    (in reality this will come well formatted and normal from the DB)
    attached_documents: extracted text from any attached documents (Can be None)
    """

    # TODO: Currently iterating thru a dict and concatenating it all, need 2 configure for proper DB input
    # Not an issue if i just get the DB to pass up a JSON obj tho
    parts = ["## Tender notice fields\n"]
    for key, value in notice_fields.items():
        if value:
            parts.append(f"- {key}: {value}")

    if attached_documents:
        parts.append("\n## Attached documents\n")
        parts.append(attached_documents.strip())

    return "\n".join(parts)


# ===
# API Call
# =========

@retry(
    retry=retry_if_exception_type(genai_errors.ServerError),  # 503s etc NOT client errors
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=2, max=30),  # 2s, 4s, 8s, ~16s
    reraise=True,
)

def summarise_tender(notice_fields: dict, attached_documents: str | None = None) -> TenderSummary:
    prompt = build_prompt(notice_fields, attached_documents)

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
    documents. Pulled out on its own so now so the field extractor can reuse the same mapped facts instead of re-running map_document
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


def summarise_tender_full(notice_fields: dict, documents_dir: str) -> TenderSummary:
    """
    Full process for a tender with multiple attatched documents; maps each
    document to a compact list of facts and drops irrelivant documents, 
    and only then does it produce the final headline + body item
    """
    combined_document_text = gather_document_facts(documents_dir)
    return summarise_tender(notice_fields, combined_document_text)




# Dummy hardcoded run to test output with the given prompt
if __name__ == "__main__":

    # Like from the tender's landing page
    notice_fields = {
        "ATM ID": "Notice 2 - 25/3151",
        "Title": "Notice of Intended Procurement - Bulk Fuel Farm",
        "Agency": "Department of Climate Change, Energy, the Environment and Water",
        "Category": "24110000 - Containers and storage",
        "Close Date & Time": "18-Aug-2026 2:00 pm (ACT Local Time)",
        "Publish Date": "21-Jul-2026",
        "Location": "ACT, NSW, VIC, SA, WA, QLD, NT, TAS",
        "ATM Type": "Notice",
        "Description": (
            "This is a pre-release notice only to provide advance notification "
            "that the Australian Antarctic Division (AAD) of the Department of "
            "Climate Change, Energy, the Environment and Water anticipates that "
            "it will commence a single stage, open tender process in Q3 2026 for "
            "the supply of components for a Special Antarctic Blend (SAB) Bulk "
            "Storage Infrastructure to support the Macquarie Island Station "
            "Project (MISP)."
        ),
    }

    # Expects a directory of .txt files, one per attached document
    summary = summarise_tender_full(notice_fields, "tenders/dummy")

    print("HEADLINE:\n", summary.headline)
    print("\nDESCRIPTION:\n", summary.description)