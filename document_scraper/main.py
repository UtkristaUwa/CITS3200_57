import os
import fitz #pdf extraction
def extract_pdf(file_path: str) :
    """
    Extracts text from a PDF file using PyMuPDF.
    returns text from pdf as a string or "" if fails
    """
    text_in_doc = ""
    try:
        with fitz.open(file_path) as pdf_doc:
            for page_num in range(len(pdf_doc)):
                page = pdf_doc[page_num]

                text_in_doc += page.get_text("text") + "\n"
                #print(text_in_doc)
        return text_in_doc
    except Exception as e:
        print(f"     ❌ PDF Extraction Error on {file_path}: {e}")
        return ""
def extract_docx(file_path: str) :
    """
    Extracts text from a Word document using python-docx.
    """
    pass
    return ""

def append_to_master_txt(master_file, text_to_append, original_document_file):
    """

    :param master_file: the web scraped text + any already extracted text from other docs
    :param text_to_append: text scraped out of pdf or docx to be added to master file
    :return:
    """
    if not text_to_append or not text_to_append.strip():
        print(f"    -> skipping append: No text found in {original_document_file}")
        return
    try:
        #open in append mode ("A")
        with open(master_file, "a", encoding="utf-8") as f:

            #write clear header for LLM context
            f.write(f"\n\n{'=' * 60}\n")
            f.write(f"DOCUMENT ATTACHMENT: {original_document_file}\n")
            f.write(f"{'=' * 60}\n\n")

            #append actual text
            f.write(text_to_append)
            f.write("\n")
        print(f"     ✅ Successfully appended {len(text_to_append)} chars from {original_document_file}.")
    except Exception as e:
        print(f"     ❌ Failed to append to {master_file}: {e}")
def process_tenders(filepath):
    """
    Iterates all tender directories checking for documents,
    """
    print("processing tenders")
    for item in os.listdir(filepath):
        tender_path = os.path.join(filepath, item)

        # Check if the path is actually a directory before looking inside
        if not os.path.isdir(tender_path):
            print(f"Skipped file: {item}")
        else:#item is a single tender directory

            print("directory: ", tender_path)
            #print(os.listdir(tender_path))
            scraped_txt_file = os.path.join(tender_path, f"{item}.txt")#txt file containing scraped info from web

            for doc in os.listdir(tender_path):
                doc_path = os.path.join(tender_path,doc)
                _, file_extension = os.path.splitext(doc)
                file_extension = file_extension.lower()

                if file_extension==".pdf":
                    print(f"  -> [PDF] Processing: {doc}")
                    result = extract_pdf(doc_path)
                    append_to_master_txt(scraped_txt_file, result, doc)


                elif file_extension == '.docx':
                    print(f"  -> [DOCX] Processing: {doc}")
                    result = extract_docx(doc_path)
                elif file_extension == '.txt':
                    #check if downloaded txt might perhaps be different to the txt file scraped off website
                    print(f"  -> [TXT] Processing: {doc}")
                    if doc == f"{item}.txt":#the web scraped text in txt file
                        #print("This is the metadata text file!")
                        pass
                    else:
                        print("txt file downloaded, must scan this too")
                        print(f"  -> [TXT] Processing: {doc}")




if __name__ == "__main__":
    # todo replace with filepath to tender data when hosted
    current_dir = os.getcwd()
    parent_directory = os.path.dirname(current_dir)

    # Safely join the parent directory with the target folder
    target_directory = os.path.join(parent_directory, "tenders_data")

    # process tender data directory
    process_tenders(target_directory)