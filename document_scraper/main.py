import os
def extract_pdf(file_path: str) :
    """
    Extracts text from a PDF file using PyMuPDF.
    """
    pass

def extract_docx(file_path: str) :
    """
    Extracts text from a Word document using python-docx.
    """
    pass

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

            for doc in os.listdir(tender_path):
                doc_path = os.path.join(tender_path,doc)
                _, file_extension = os.path.splitext(doc)
                file_extension = file_extension.lower()

                if file_extension==".pdf":
                    print(f"  -> [PDF] Processing: {doc}")
                    result = extract_pdf(doc_path)

                elif file_extension == '.docx':
                    print(f"  -> [DOCX] Processing: {doc}")
                    result = extract_docx(doc_path)
                elif file_extension == '.txt':
                    #check if downloaded txt might perhaps be different to the txt file scraped off website
                    print(f"  -> [TXT] Processing: {doc}")




if __name__ == "__main__":
    # todo replace with filepath to tender data when hosted
    current_dir = os.getcwd()
    parent_directory = os.path.dirname(current_dir)

    # Safely join the parent directory with the target folder
    target_directory = os.path.join(parent_directory, "tenders_data")

    # process tender data directory
    process_tenders(target_directory)