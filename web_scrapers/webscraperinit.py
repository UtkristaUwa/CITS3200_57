import os
import re
import time
from urllib.parse import unquote, urljoin
from bs4 import BeautifulSoup
import httpx

# ==============================================================================
# 1. CONFIGURATION & CREDENTIALS
# ==============================================================================
DEBUG = True

USERNAME = "gumdrop.quips-5i@icloud.com"
PASSWORD = "tatzaz-xafdyF-1tikhy"

BASE_URL = "https://www.tenders.gov.au"
# Corrected AusTender User Auth Endpoint:
LOGIN_URL = f"{BASE_URL}/RegisteredUser/Login"
ATM_LIST_URL = f"{BASE_URL}/atm"

MAX_TENDERS_LIMIT = 10
OUTPUT_DIR = "tenders_data"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def debug(message: str) -> None:
    if DEBUG:
        print(message)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


# ==============================================================================
# 2. ENHANCED AUTHENTICATION MODULE
# ==============================================================================
def login_to_austender(client: httpx.Client) -> bool:
    """
    Authenticates against AusTender at /RegisteredUser/Login.
    Inspects the login form, extracts all hidden inputs/tokens,
    and submits credentials while printing diagnostic traces.
    """
    debug("=" * 70)
    debug("[AUTH STEP 1] Fetching login page...")
    debug(f"[AUTH TARGET] URL: {LOGIN_URL}")

    try:
        res = client.get(LOGIN_URL, headers=HEADERS, timeout=20.0)
        debug(f"[AUTH STEP 1] Status Code: {res.status_code}")
        debug(f"[AUTH STEP 1] Final URL after redirects: {res.url}")

        if res.status_code != 200:
            debug(f"[AUTH ERROR] Failed to load login page. HTTP {res.status_code}")
            return False

        soup = BeautifulSoup(res.text, "html.parser")

        # Locate the login form
        form = soup.select_one("form[action*='Login'], form")
        if not form:
            debug("[AUTH ERROR] Could not find any <form> tag on the login page.")
            return False

        debug(f"[AUTH STEP 2] Found Form. Action = {form.get('action')}, Method = {form.get('method')}")

        # Harvest all hidden input fields (e.g., __RequestVerificationToken)
        payload = {}
        for hidden_input in form.select("input[type='hidden']"):
            name = hidden_input.get("name")
            value = hidden_input.get("value", "")
            if name:
                payload[name] = value
                debug(f"  [AUTH TOKEN FOUND] {name} = {value[:25]}..." if len(
                    value) > 25 else f"  [AUTH TOKEN FOUND] {name} = {value}")

        # Inspect input field names for email & password
        email_input = form.select_one(
            "input[type='text'], input[type='email'], input[name*='Email'], input[name*='User']")
        pass_input = form.select_one("input[type='password'], input[name*='Pass']")

        email_field_name = email_input.get("name", "Email") if email_input else "Email"
        pass_field_name = pass_input.get("name", "Password") if pass_input else "Password"

        debug(f"  [AUTH FIELD MAP] Username Key: '{email_field_name}' | Password Key: '{pass_field_name}'")

        payload[email_field_name] = USERNAME
        payload[pass_field_name] = PASSWORD

        # Resolve submission target URL
        action_attr = form.get("action")
        post_url = urljoin(BASE_URL, action_attr) if action_attr else LOGIN_URL

        debug(f"[AUTH STEP 3] Submitting POST credentials to: {post_url}")

        # AusTender requires a Referer header on auth POST
        post_headers = HEADERS.copy()
        post_headers["Referer"] = str(res.url)

        login_res = client.post(
            post_url,
            data=payload,
            headers=post_headers,
            follow_redirects=True,
            timeout=25.0,
        )

        debug(f"[AUTH STEP 3] Post Response Status: {login_res.status_code}")
        debug(f"[AUTH STEP 3] Post Final URL: {login_res.url}")
        debug(f"[AUTH COOKIES] Session Cookie Jar: {dict(client.cookies)}")

        # Verification check
        page_text = login_res.text.lower()
        if "log off" in page_text or "logout" in page_text or USERNAME.lower() in page_text:
            debug("[AUTH SUCCESS] AusTender session successfully established!")
            debug("=" * 70)
            return True
        elif "invalid" in page_text or "incorrect" in page_text:
            debug("[AUTH FAILED] Server indicated invalid username or password.")
            return False
        else:
            debug("[AUTH NOTICE] Credentials submitted. Proceeding with active session cookies.")
            debug("=" * 70)
            return True

    except Exception as exc:
        debug(f"[AUTH EXCEPTION] Error during authentication flow: {exc}")
        return False


# ==============================================================================
# 3. DOCUMENT DOWNLOADER MODULE
# ==============================================================================
def download_tender_documents(client: httpx.Client, doc_page_url: str, tender_folder: str) -> None:
    """Discovers and downloads soft-copy attachments and addenda."""
    debug(f"    [DOCS] Scanning document list page: {doc_page_url}")
    try:
        res = client.get(doc_page_url, headers=HEADERS, timeout=20.0)
        debug(f"    [DOCS] Page Response HTTP {res.status_code} | Final URL: {res.url}")

        if res.status_code != 200:
            debug(f"    [WARN] Failed to load documents page: HTTP {res.status_code}")
            return

        soup = BeautifulSoup(res.text, "html.parser")

        # Check if redirected to login
        if "login" in str(res.url).lower() or soup.select_one("input[type='password']"):
            debug("    [AUTH REQUIRED] Access denied. Redirected to login page. Session not active.")
            return

        doc_links = soup.select("a[href*='/Atm/DownloadSoftCopy/'], a[href*='/Atm/DownloadAddenda/']")
        debug(f"    [DOCS] Located {len(doc_links)} downloadable attachments.")

        for a_tag in doc_links:
            relative_href = a_tag.get("href")
            if not relative_href:
                continue

            file_url = urljoin(BASE_URL, relative_href)
            file_title = a_tag.get("title")

            if not file_title:
                match = re.search(r"fileName=([^&]+)", relative_href)
                file_title = unquote(match.group(1)) if match else "document.bin"

            clean_file_name = sanitize_filename(file_title)
            save_path = os.path.join(tender_folder, clean_file_name)

            debug(f"      -> Downloading: '{clean_file_name}'")

            with client.stream("GET", file_url, headers=HEADERS, timeout=60.0) as stream_resp:
                if stream_resp.status_code == 200:
                    with open(save_path, "wb") as f_out:
                        for chunk in stream_resp.iter_bytes(chunk_size=8192):
                            f_out.write(chunk)
                    debug(f"         [SAVED] {clean_file_name}")
                else:
                    debug(f"         [FAILED] HTTP {stream_resp.status_code} on download.")

            time.sleep(0.3)

    except Exception as exc:
        debug(f"    [DOCS ERROR] Error while processing documents: {exc}")


# ==============================================================================
# 4. FULL DETAILS PAGE PARSER
# ==============================================================================
def process_tender_details(client: httpx.Client, detail_url: str, output_dir: str) -> None:
    """Scrapes metadata from the Full Details page, writes TXT, and downloads docs."""
    debug(f"\n  [HTTP GET] Fetching Full Details: {detail_url}")
    try:
        res = client.get(detail_url, headers=HEADERS, timeout=20.0)
        if res.status_code != 200:
            debug(f"  [ERROR] Could not load details (HTTP {res.status_code})")
            return

        soup = BeautifulSoup(res.text, "html.parser")

        # 1. Title
        title_elem = soup.select_one("p.lead, h1")
        title = title_elem.get_text(strip=True) if title_elem else "Untitled Tender"

        # 2. Extract structured key-value pairs
        metadata = {}
        for block in soup.select("div.list-desc"):
            label_elem = block.select_one("span > label, label, span")
            val_elem = block.select_one("div.list-desc-inner")
            if label_elem and val_elem:
                clean_key = label_elem.get_text(strip=True).replace(":", "")
                clean_val = val_elem.get_text(separator=" ", strip=True)
                metadata[clean_key] = clean_val

        atm_id = metadata.get("ATM ID")
        if not atm_id:
            atm_id = detail_url.rstrip("/").split("/")[-1]
            metadata["ATM ID"] = atm_id

        debug(f"  [PARSED] ATM ID: {atm_id} | Title: {title[:40]}...")

        # 3. Create unique tender folder
        folder_name = sanitize_filename(atm_id)
        tender_folder = os.path.join(output_dir, folder_name)
        os.makedirs(tender_folder, exist_ok=True)

        # 4. Save metadata TXT
        txt_filename = os.path.join(tender_folder, f"{folder_name}.txt")
        with open(txt_filename, "w", encoding="utf-8") as f:
            f.write("=" * 80 + "\n")
            f.write(f"AUSTENDER DETAILS: {title}\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Detail URL: {detail_url}\n")
            f.write(f"Scraped At: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("STRUCTURED ATTRIBUTES:\n")
            f.write("-" * 40 + "\n")
            for k, v in metadata.items():
                f.write(f"{k}: {v}\n")
            f.write("\n" + "=" * 80 + "\n")

        debug(f"  [SAVED] Tender summary written to '{txt_filename}'")

        # 5. Extract documents
        docs_btn = soup.select_one("a.rBtn[href*='/Atm/ViewDocuments/'], a[href*='/Atm/ViewDocuments/']")
        if docs_btn:
            doc_page_url = urljoin(BASE_URL, docs_btn.get("href"))
            download_tender_documents(client, doc_page_url, tender_folder)
        else:
            debug("  [NOTICE] No 'ATM Documents' link present for this tender.")

    except Exception as exc:
        debug(f"  [ERROR] Error processing tender details: {exc}")


# ==============================================================================
# 5. MAIN ORCHESTRATOR
# ==============================================================================
def run_scraper(limit: int = 10, output_dir: str = "tenders_data") -> None:
    os.makedirs(output_dir, exist_ok=True)
    debug("=" * 75)
    debug(f"STARTING AUSTENDER EXTRACTION (Targeting First {limit} Tenders)")
    debug("=" * 75)

    with httpx.Client(follow_redirects=True) as client:
        # Step 1: Log in
        is_logged_in = login_to_austender(client)
        if not is_logged_in:
            debug("[WARNING] Authentication failed. Continuing, but restricted downloads may fail.\n")

        page_num = 1
        tenders_scraped = 0

        while tenders_scraped < limit:
            list_page_url = f"{ATM_LIST_URL}?page={page_num}"
            debug(f"\n[PAGE START] Loading list page: {list_page_url}")

            res = client.get(list_page_url, headers=HEADERS, timeout=20.0)
            if res.status_code != 200:
                debug(f"[ERROR] Failed to load list page {page_num} (HTTP {res.status_code})")
                break

            soup = BeautifulSoup(res.text, "html.parser")
            detail_links = soup.select("a[href*='/Atm/Show/']")

            seen_links = set()
            unique_links = []
            for a in detail_links:
                href = a.get("href")
                if href and href not in seen_links:
                    seen_links.add(href)
                    unique_links.append(urljoin(BASE_URL, href))

            if not unique_links:
                debug("[NOTICE] No tender links found. End of list reached.")
                break

            for full_detail_url in unique_links:
                if tenders_scraped >= limit:
                    break

                tenders_scraped += 1
                debug(f"\n[{tenders_scraped}/{limit}] Ingesting Tender...")
                process_tender_details(client, full_detail_url, output_dir=output_dir)
                time.sleep(0.5)

            page_num += 1

    debug("\n" + "=" * 75)
    debug(f"SCRAPING COMPLETE: Processed {tenders_scraped} tenders into '{OUTPUT_DIR}/'.")
    debug("=" * 75)


if __name__ == "__main__":
    run_scraper(limit=MAX_TENDERS_LIMIT)