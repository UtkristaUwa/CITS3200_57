#File holding our helper functions that are used across all scrapers. Also includes the status error codes for failures.

import os
import re
import fitz
import docx

#Error status codes for failures
#Scraping was a total success
SITE_SUCCESS = 0
#The URL provided couldnt be reached
SITE_TOTAL_FAILURE = 1
#The site login failed
SITE_LOGIN_FAILED = 2
#Anti bot detections have blocked us
SITE_BOT_BLOCKED = 3
#The structure of the website has changed thus causing our scraper to fail
SITE_STRUCTURE_CHANGE = 4
#Site has temporarily blocked our access
SITE_RATE_LIMITED = 5
#A tender only has partial information gathered and needs to be verified
TENDER_PARTIAL = 6

#-----
#This is our folder and filename handling for the tenders
#-----

#This function is specifically for returning the folder path for a tender, and creating one if there isn't one.
#All of a tenders output, the page html, attachments, and extracted attachement text lives here and is named after the tender ID
def tender_dir(tender_id: str, base_dir: str = "tenders_data") -> str:
    path = os.path.join(base_dir, sanitise_filename(tender_id))
    os.makedirs(path, exist_ok=True)
    return path

#This function strips anything that could escape the tender folder or break the filesystem as we cant trust a name scraped off a page
def sanitise_filename(name: str) -> str:
    name = name.replaced("/", "_").replace("\\", "_")
    return re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip()

#-----
#This is the section that saves the per tender output
#-----

#This function writes the scraped page content html content
def save_page_text(folder: str, tender_id: str, text: str) -> None:
    path = os.path.join(folder, f"{sanitise_filename(tender_id)}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

#This function focusses on the downloaded attachment from the tender page
def save_attachment(folder: str, filename: str, content: bytes) -> str:
    path = os.path.join(folder, sanitise_filename(filename))
    with open(path, "wb") as f:
        f.write(content)
    return path

#This function is for extracted the attachment file's contents, we also make sure the file names match between the attachement and extracted information
def save_extracted_text(folder: str, attachment_filename: str, text: str) -> str:
    base, _ = os.path.splitext(sanitise_filename(attachment_filename))
    path = os.path.join(folder, f"{base}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path

#-----
#These are the helper functions that help with the attachement information extraction
#-----

#This class is raised when the attachement cant be read for text
class ExtractionError(Exception):

#This is our pdf extraction code
def extract_pdf(file_path: str) -> str:
    try:
        text = ""
        with fitz.open(file_path) as pdf_doc:
            for page in pdf_doc:
                text += page.get_text("text") + "\n"
        return text
    except Exception as e:
        raise ExtractionError(f"PDF extraction failed on {file_path}: {e}") from e

#This helps with our word document extraction
def extract_docx(file_path: str) -> str:
    try:
        text = ""
        doc = docx.Document(file_path)
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text += paragraph.text + "\n"
        return text
    except Exception as e:
        raise ExtractionError(f"DOCX extraction failed on {file_path}: {e}") from e