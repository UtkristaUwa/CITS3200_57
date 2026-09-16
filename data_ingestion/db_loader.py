from google.cloud import bigquery
import logging

# Set base importance of what we report to the console
logging.basicConfig(level=logging.INFO)

def upload_tender(record: dict, table_ref: str):
    """
    Takes a JSON dictionary and inserts it as a record/row into the specified BigQuery table
    In our case we are taking the output of a single tender directly from the data ingestion process
    """

    # The client_id is the first part of the table ref
    client = bigquery.Client(project=table_ref.split('.')[0])
    
    # Insert_rows_json expects a list of dicts (records), in our case a single item list
    errors = client.insert_rows_json(table_ref, [record])

    # If everything is good, errors will simply be an empty list [].
    if not errors:
        logging.info(f"Successfully inserted record into {table_ref}")
    else:
        logging.error(f"Failed to insert record into {table_ref}: {errors}")
        raise RuntimeError(f"BigQuery insertion failed: {errors}")