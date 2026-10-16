"""
Ingest telemetry CSVs into Azure Data Explorer (Kusto).

Setup:
  1. Create a free cluster at https://dataexplorer.azure.com/freecluster
  2. Create the table by running kql/00_setup.kql in the ADX web UI.
  3. Set env vars: ADX_CLUSTER (ingest URI, e.g. https://ingest-<name>.<region>.kusto.windows.net),
     ADX_DATABASE, and sign in with `az login` (or use the web UI's "Get data" instead).

Usage:
    python ingest.py data/fleet_telemetry.csv data/local_telemetry.csv

Alternative with no code: ADX web UI -> your database -> Get data -> Local file.
"""
import os
import sys

from azure.kusto.data import KustoConnectionStringBuilder
from azure.kusto.data.data_format import DataFormat
from azure.kusto.ingest import IngestionProperties, QueuedIngestClient

TABLE = "DeviceTelemetry"


def main(paths):
    cluster = os.environ["ADX_CLUSTER"]
    database = os.environ["ADX_DATABASE"]
    kcsb = KustoConnectionStringBuilder.with_az_cli_authentication(cluster)
    client = QueuedIngestClient(kcsb)
    props = IngestionProperties(
        database=database,
        table=TABLE,
        data_format=DataFormat.CSV,
        ignore_first_record=True,  # skip CSV header
    )
    for p in paths:
        print(f"Queueing {p} -> {database}.{TABLE}")
        client.ingest_from_file(p, ingestion_properties=props)
    print("Queued. Ingestion can take a minute or two; check with: DeviceTelemetry | count")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python ingest.py <csv> [<csv> ...]")
    main(sys.argv[1:])
