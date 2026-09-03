import os
import sys
import tempfile
import logging
from typing import Dict

# Import your web scraper and document scraper functions
# (Adjust the import names to match your actual python files)
from web_scrapers.webscraperinit import run_scraper as run_web_scraper
from document_scraper.main import process_tenders as run_doc_scraper

# Improt tender processing code
from data_ingestion.tender_processor import process_tender

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("Manager")


def main():
    logger.info("Starting Daily Tender Pipeline...")

    # 1. Spin up a temporary ephemeral directory in container memory
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Created temporary working directory: {temp_dir}")

        # 2. Run the Web Scraper
        # We pass the temp_dir so it downloads HTML metadata and PDFs directly into RAM
        logger.info("Executing Web Scraper...")
        try:
            run_web_scraper(limit=10, output_dir=temp_dir)
        except Exception as e:
            logger.error(f"Web scraper failed: {e}")
            sys.exit(1)

        # 3. Run the Document Scraper
        # It scans temp_dir, parses PDFs/DOCXs, and creates individual .txt files
        logger.info("Executing Document Scraper...")
        try:
            run_doc_scraper(temp_dir)
        except Exception as e:
            logger.error(f"Document scraper failed: {e}")
            sys.exit(1)

        # 4. Gather all .txt files and pass to Data Processing
        logger.info("Preparing data for AI Processing...")

        # Iterate over each tender folder created inside the temp directory
        for tender_folder_name in os.listdir(temp_dir):
            tender_path = os.path.join(temp_dir, tender_folder_name)

            if not os.path.isdir(tender_path):
                continue

            logger.info(f"Extracting texts for tender: {tender_folder_name}")

            # Dictionary to hold filename -> file content
            extracted_texts: Dict[str, str] = {}

            # # Read the web-scraped metadata .txt and the document .txt files
            # for filename in os.listdir(tender_path):
            #     if filename.endswith(".txt"):
            #         file_path = os.path.join(tender_path, filename)
            #         with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            #             extracted_texts[filename] = f.read()

            # if not extracted_texts:
            #     logger.warning(f"No text files found for {tender_folder_name}. Skipping.")
            #     continue

            # Run tender processing on current tender
            try:
                print(process_tender(tender_path))
            except Exception as e:
                logger.error(f"Tender processing failed for {tender_folder_name}: {e}")
                continue

            # ==================================================================
            # TODO: call this canton
            # Pass the tender ID and the dictionary of text files to the data processor
            # Example: data_processing_function(tender_id=tender_folder_name, texts=extracted_texts)
            # ==================================================================

    # Once the 'with' block ends, Python permanently deletes the temp_dir and all files inside it.
    logger.info("Pipeline finished. Temporary files wiped from memory.")


if __name__ == "__main__":
    main()