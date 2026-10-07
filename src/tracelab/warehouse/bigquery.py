"""Load the lake into BigQuery tables and run the rollups in BigQuery's own dialect.

The client takes an endpoint, so the same code points at real BigQuery with
credentials or at the BigQuery emulator with none. Everything in this
repository has been run against both the emulator and BigQuery itself, in a
GCP project; docs/warehouse.md has what each run established.

The tables are created partitioned by run date and clustered on the columns
every rollup filters or groups by, which is what keeps a scan over a long
history proportional to the slice asked for rather than to the whole table.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from google.api_core.client_options import ClientOptions
from google.auth.credentials import AnonymousCredentials
from google.cloud import bigquery

SQL = Path(__file__).parent / "sql"
ROLLUPS = ("daily_metric_rollup", "run_health", "run_to_run_noise")

TYPES = {
    pa.string(): "STRING",
    pa.int64(): "INT64",
    pa.float64(): "FLOAT64",
    pa.bool_(): "BOOL",
    pa.timestamp("us", tz="UTC"): "TIMESTAMP",
}
CLUSTERING = {
    "runs": ["benchmark", "hardware_class", "status"],
    "samples": ["benchmark", "hardware_class", "metric_name"],
}


@dataclass(frozen=True)
class Warehouse:
    client: bigquery.Client
    dataset: str
    emulated: bool = False
    location: str = "US"

    @classmethod
    def connect(cls, project: str, dataset: str, endpoint: str | None = None) -> Warehouse:
        if endpoint is None:
            return cls(bigquery.Client(project=project), dataset)
        client = bigquery.Client(
            project=project,
            credentials=AnonymousCredentials(),  # type: ignore[no-untyped-call]
            client_options=ClientOptions(api_endpoint=endpoint),
        )
        return cls(client, dataset, emulated=True)

    @property
    def qualified(self) -> str:
        return f"{self.client.project}.{self.dataset}"

    def create(self, lake: Path) -> None:
        dataset = bigquery.Dataset(self.qualified)
        dataset.location = self.location
        self.client.create_dataset(dataset, exists_ok=True)
        for name in CLUSTERING:
            table = bigquery.Table(f"{self.qualified}.{name}", schema=_schema(lake / name))
            # Partitioned on the run's start time, the column every history
            # query bounds first.
            table.time_partitioning = bigquery.TimePartitioning(field="started_at")
            table.clustering_fields = CLUSTERING[name]
            self.client.create_table(table, exists_ok=True)

    def load(self, lake: Path, batch: int = 5000) -> dict[str, int]:
        if not self.emulated:
            return self._load_jobs(lake)
        loaded: dict[str, int] = {}
        for name in CLUSTERING:
            total = 0
            for rows in _batches(lake / name, batch):
                errors = self.client.insert_rows_json(f"{self.qualified}.{name}", rows)
                if errors:
                    raise RuntimeError(f"{name}: {errors[:3]}")
                total += len(rows)
            loaded[name] = total
        return loaded

    def _load_jobs(self, lake: Path) -> dict[str, int]:
        """Load each table with one Parquet load job, replacing what was there.

        Load jobs are free where streaming inserts are not, and truncating
        makes a rerun idempotent instead of doubling every row. The date
        column lives in the lake's directory names, so the table is read
        through the partitioning and written as one file that carries it.
        """
        loaded: dict[str, int] = {}
        for name in CLUSTERING:
            table = ds.dataset(lake / name, format="parquet", partitioning="hive").to_table()
            table = table.set_column(
                table.schema.get_field_index("date"),
                "date",
                table.column("date").cast(pa.string()),
            )
            buffer = io.BytesIO()
            pq.write_table(table, buffer)
            buffer.seek(0)
            config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.PARQUET,
                write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            )
            job = self.client.load_table_from_file(
                buffer, f"{self.qualified}.{name}", job_config=config, location=self.location
            )
            job.result()
            loaded[name] = table.num_rows
        return loaded

    def rollup(self, name: str) -> list[dict[str, Any]]:
        query = (SQL / f"{name}.sql").read_text().format(dataset=self.qualified)
        return [dict(row.items()) for row in self.client.query(query).result()]


def _schema(path: Path) -> list[bigquery.SchemaField]:
    arrow = ds.dataset(path, format="parquet", partitioning="hive").schema
    return [
        bigquery.SchemaField(f.name, TYPES[pa.string() if f.name == "date" else f.type])
        for f in arrow
    ]


def _batches(path: Path, size: int) -> Iterator[list[dict[str, Any]]]:
    dataset = ds.dataset(path, format="parquet", partitioning="hive")
    for record_batch in dataset.to_batches(batch_size=size):
        rows = record_batch.to_pylist()
        for row in rows:
            for key, value in row.items():
                if isinstance(value, datetime):
                    row[key] = value.isoformat()
        yield rows
