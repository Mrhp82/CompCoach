"""Explicit, one-shot migration of a CompCoach SQLite database to Supabase.

The default is a local dry run. ``--apply`` copies into an empty CompCoach
PostgreSQL schema in one transaction; it never modifies the source database.
Only counts are printed, never credentials, link tokens, or athlete names.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import tempfile
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from compcoach_live.asset_store import validate_image
    from compcoach_live.storage import CompCoachDB
except ModuleNotFoundError:  # direct ``python migrate_to_supabase.py``
    from asset_store import validate_image
    from storage import CompCoachDB


class MigrationError(RuntimeError):
    """A safe operational message that does not include stored row values."""


@dataclass(frozen=True)
class AssetToMigrate:
    competition_id: str
    column: str
    path: Path
    payload: bytes


@contextmanager
def source_snapshot(path: str | Path, *, backup_file: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Back up a read-only source, then upgrade only the temporary snapshot."""
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        raise MigrationError("Il database SQLite indicato non esiste.")
    with tempfile.TemporaryDirectory(prefix="compcoach-migration-") as directory:
        snapshot_path = Path(directory) / "snapshot.db"
        with sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(snapshot_path) as destination:
                source.backup(destination)
        if backup_file is not None:
            backup_path = Path(backup_file).expanduser().resolve()
            if backup_path.exists():
                raise MigrationError("Il file di backup esiste già; scegli un nome nuovo.")
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                backup = backup_path.open("xb")
            except FileExistsError as exc:
                raise MigrationError("Il file di backup esiste già; scegli un nome nuovo.") from exc
            try:
                with backup, snapshot_path.open("rb") as original:
                    shutil.copyfileobj(original, backup)
            except BaseException:
                backup_path.unlink(missing_ok=True)
                raise
        # Handles earlier releases without changing the original DB or WAL.
        CompCoachDB(snapshot_path)
        with sqlite3.connect(snapshot_path) as snapshot:
            snapshot.row_factory = sqlite3.Row
            snapshot.execute("PRAGMA foreign_keys = ON")
            if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MigrationError("Controllo integrità SQLite fallito.")
            if snapshot.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise MigrationError("Il database contiene riferimenti non validi.")
            yield snapshot


def quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def ordered_tables(snapshot: sqlite3.Connection) -> list[str]:
    """Order all domain tables by their FK dependencies, rejecting cycles."""
    table_names = {
        row[0]
        for row in snapshot.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    }
    dependencies: dict[str, set[str]] = {}
    for table in table_names:
        dependencies[table] = {
            row["table"]
            for row in snapshot.execute(
                f"PRAGMA foreign_key_list({quote_sqlite_identifier(table)})"
            )
            if row["table"] != table
        }
        if not dependencies[table].issubset(table_names):
            raise MigrationError("Una tabella SQLite dipende da una tabella mancante.")
    ordered: list[str] = []
    while dependencies:
        ready = sorted(table for table, refs in dependencies.items() if not refs)
        if not ready:
            raise MigrationError("Schema con dipendenze circolari non supportato.")
        ordered.extend(ready)
        for table in ready:
            del dependencies[table]
        for refs in dependencies.values():
            refs.difference_update(ready)
    return ordered


def table_rows(snapshot: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = [
        dict(row)
        for row in snapshot.execute(f"SELECT * FROM {quote_sqlite_identifier(table)}")
    ]
    # Successor days reference the previous day in the same table. PostgreSQL
    # checks that FK at each insert, so insert ancestors before successors.
    self_refs = [
        (row["from"], row["to"])
        for row in snapshot.execute(
            f"PRAGMA foreign_key_list({quote_sqlite_identifier(table)})"
        )
        if row["table"] == table
    ]
    if not self_refs:
        return rows
    pending = rows[:]
    ordered: list[dict[str, Any]] = []
    seen: dict[str, set[Any]] = {column: set() for _, column in self_refs}
    while pending:
        ready = [
            row
            for row in pending
            if all(row[source] is None or row[source] in seen[target] for source, target in self_refs)
        ]
        if not ready:
            raise MigrationError("Una sequenza di giornate contiene riferimenti circolari.")
        ordered.extend(ready)
        for row in ready:
            for column in seen:
                seen[column].add(row[column])
        ready_ids = {id(row) for row in ready}
        pending = [row for row in pending if id(row) not in ready_ids]
    return ordered


def migration_assets(snapshot: sqlite3.Connection, root: str | Path) -> list[AssetToMigrate]:
    """Read and validate every referenced local asset before touching target."""
    asset_root = Path(root).expanduser().resolve()
    assets: list[AssetToMigrate] = []
    for row in snapshot.execute("SELECT id, logo_path, strip_map_path FROM competitions"):
        for column in ("logo_path", "strip_map_path"):
            reference = str(row[column] or "")
            if not reference:
                continue
            if "://" in reference:
                raise MigrationError(
                    "La sorgente contiene immagini remote: questa procedura importa solo immagini locali."
                )
            relative_path = Path(reference)
            if relative_path.is_absolute():
                raise MigrationError("Un riferimento immagine non è relativo alla cartella assets.")
            path = (asset_root / relative_path).resolve()
            if not path.is_relative_to(asset_root):
                raise MigrationError("Un riferimento immagine esce dalla cartella assets.")
            if not path.is_file():
                raise MigrationError(
                    "Manca un logo o una mappa referenziati. Controlla --assets-dir prima di riprovare."
                )
            payload = path.read_bytes()
            validate_image(payload, filename=path.name)
            assets.append(AssetToMigrate(str(row["id"]), column, path, payload))
    return assets


def migrate(
    snapshot: sqlite3.Connection,
    target: Any,
    assets: list[AssetToMigrate],
    *,
    asset_store: Any = None,
) -> dict[str, int]:
    """Copy exact rows into an empty target, retaining IDs, tokens and history.

    Target schema initialization may occur before this call; all imported data
    is committed together. Uploaded Storage objects are compensatingly removed
    if the database copy fails. No pre-existing object is overwritten.
    """
    from psycopg import sql

    tables = ordered_tables(snapshot)
    counts = {
        table: int(snapshot.execute(f"SELECT COUNT(*) FROM {quote_sqlite_identifier(table)}").fetchone()[0])
        for table in tables
    }
    if assets and asset_store is None:
        raise MigrationError("Configura Supabase Storage per copiare logo e mappa.")
    uploaded: list[str] = []
    committed = False
    commit_attempted = False
    try:
        with target._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            raw = connection.raw
            target_columns: dict[str, set[str]] = {}
            for row in raw.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = %s ORDER BY table_name, ordinal_position",
                (target.schema,),
            ).fetchall():
                target_columns.setdefault(row["table_name"], set()).add(row["column_name"])
            # Lock every target domain table before checking emptiness. A
            # concurrent first app write cannot race the import's empty check.
            for table in sorted(target_columns):
                raw.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(target.schema, table)))
                occupied = raw.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(sql.Identifier(target.schema, table))).fetchone()
                if occupied:
                    raise MigrationError(
                        "Il database di destinazione contiene già dati. Nessuna riga è stata importata. "
                        "Usa un progetto CompCoach nuovo e vuoto; questa procedura non cancella dati."
                    )
            # Schema-only bootstrap is transactional too; do not run domain
            # backfills on a populated destination before the empty guard.
            initializer = getattr(target, "initialize_schema", None)
            if initializer is not None:
                initializer(connection)
                target_columns = {}
                for row in raw.execute(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position",
                    (target.schema,),
                ).fetchall():
                    target_columns.setdefault(row["table_name"], set()).add(row["column_name"])
            missing = set(tables) - target_columns.keys()
            if missing:
                raise MigrationError("Schema PostgreSQL incompleto; aggiorna il codice prima di migrare.")
            for table in tables:
                columns = [row["name"] for row in snapshot.execute(f"PRAGMA table_info({quote_sqlite_identifier(table)})")]
                if not set(columns).issubset(target_columns[table]):
                    raise MigrationError("Le colonne SQLite e PostgreSQL non coincidono; migrazione interrotta.")
                insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(target.schema, table),
                    sql.SQL(", ").join(map(sql.Identifier, columns)),
                    sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                )
                for row in table_rows(snapshot, table):
                    raw.execute(insert, tuple(row[column] for column in columns))
            for asset in assets:
                if asset.column == "logo_path":
                    stored = asset_store.save_logo(asset.competition_id, asset.payload, filename=asset.path.name, overwrite=False)
                else:
                    stored = asset_store.save_strip_map(asset.competition_id, asset.payload, filename=asset.path.name, overwrite=False)
                uploaded.append(stored.object_key)
                raw.execute(
                    sql.SQL("UPDATE {} SET {} = %s WHERE id = %s").format(
                        sql.Identifier(target.schema, "competitions"), sql.Identifier(asset.column)
                    ),
                    (stored.object_key, asset.competition_id),
                )
            # Explicit IDs preserve audit/history. Bring identity sequences
            # forward so the next normal app write cannot reuse an imported ID.
            for table in tables:
                if "id" not in target_columns[table]:
                    continue
                sequence = raw.execute("SELECT pg_get_serial_sequence(%s, 'id') AS sequence", (f'{target.schema}.{table}',)).fetchone()["sequence"]
                if not sequence:
                    continue
                maximum = raw.execute(sql.SQL("SELECT MAX(id) AS maximum FROM {}").format(sql.Identifier(target.schema, table))).fetchone()["maximum"]
                raw.execute("SELECT setval(%s::regclass, %s, %s)", (sequence, maximum if maximum is not None else 1, maximum is not None))
            for table, expected in counts.items():
                actual = raw.execute(sql.SQL("SELECT COUNT(*) AS count FROM {}").format(sql.Identifier(target.schema, table))).fetchone()["count"]
                if actual != expected:
                    raise MigrationError("Verifica conteggio fallita; il database non è stato importato.")
            commit_attempted = True
            connection.commit()
            committed = True
    except BaseException as exc:
        if commit_attempted and not committed:
            # A dropped connection while waiting for COMMIT can mean that the
            # server committed successfully. Removing images would break those
            # committed references; keep them until the outcome is checked.
            raise MigrationError(
                "La conferma finale non è stata verificata. Non ripetere --apply automaticamente: "
                "controlla prima i dati di destinazione e Storage. Il database originale è intatto."
            ) from exc
        if not committed and uploaded:
            cleanup_failures = 0
            for reference in reversed(uploaded):
                try:
                    asset_store.delete_reference(reference)
                except Exception:
                    cleanup_failures += 1
            if cleanup_failures:
                raise MigrationError(
                    "Importazione non completata. Storage potrebbe contenere immagini caricate da questo "
                    "tentativo; controlla il bucket prima di riprovare. Nessuna riga importata è stata confermata."
                ) from exc
        raise
    return counts


def load_settings(secrets_file: str | Path | None) -> dict[str, str]:
    settings: dict[str, str] = {}
    if secrets_file is not None:
        path = Path(secrets_file).expanduser()
        if not path.is_file():
            raise MigrationError("Il file Secrets indicato non esiste.")
        with path.open("rb") as stream:
            settings.update({key: str(value) for key, value in tomllib.load(stream).items() if isinstance(value, (str, int, bool))})
    for key in ("COMPCOACH_DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SECRET_KEY", "COMPCOACH_STORAGE_BUCKET"):
        if os.environ.get(key):
            settings[key] = os.environ[key]
    return settings


def print_summary(counts: Mapping[str, int], asset_count: int, *, applied: bool) -> None:
    print("Migrazione completata." if applied else "Controllo locale completato: nessun dato remoto modificato.")
    for table, count in counts.items():
        print(f"  {table}: {count} righe")
    print(f"  immagini referenziate: {asset_count}")
    if not applied:
        print("Per importare, aggiungi --apply e configura i Secrets. Non aprire ancora l'app cloud.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path, default=Path(__file__).resolve().parent / "data" / "compcoach.db", help="Database SQLite da conservare/importare.")
    parser.add_argument("--assets-dir", type=Path, help="Cartella delle immagini; default: assets accanto al database SQLite.")
    parser.add_argument("--secrets-file", type=Path, help="File TOML privato; le variabili ambiente prevalgono.")
    parser.add_argument("--backup-file", type=Path, help="Salva anche una copia SQLite coerente prima dell'aggiornamento dello schema; non sovrascrive file.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Importa nel database remoto vuoto.")
    mode.add_argument("--dry-run", action="store_true", help="Solo controllo locale (comportamento predefinito).")
    args = parser.parse_args(argv)
    try:
        with source_snapshot(args.sqlite, backup_file=args.backup_file) as snapshot:
            tables = ordered_tables(snapshot)
            assets = migration_assets(snapshot, args.assets_dir or args.sqlite.resolve().parent / "assets")
            if not args.apply:
                counts = {table: snapshot.execute(f"SELECT COUNT(*) FROM {quote_sqlite_identifier(table)}").fetchone()[0] for table in tables}
                print_summary(counts, len(assets), applied=False)
                return 0
            settings = load_settings(args.secrets_file)
            database_url = settings.get("COMPCOACH_DATABASE_URL", "").strip()
            if not database_url:
                raise MigrationError("Manca COMPCOACH_DATABASE_URL nei Secrets o nell'ambiente.")
            try:
                from compcoach_live.postgres_storage import CompCoachPostgresDB
                from compcoach_live.asset_store import SupabaseAssetStore
            except ModuleNotFoundError:
                from postgres_storage import CompCoachPostgresDB
                from asset_store import SupabaseAssetStore
            asset_store = None
            if assets:
                url = settings.get("SUPABASE_URL", "").strip()
                key = (settings.get("SUPABASE_SECRET_KEY") or settings.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
                if not url or not key:
                    raise MigrationError("Mancano SUPABASE_URL e la chiave privata server per importare le immagini.")
                asset_store = SupabaseAssetStore(url, key, bucket=settings.get("COMPCOACH_STORAGE_BUCKET") or "compcoach-assets")
            target = CompCoachPostgresDB(database_url, initialize=False)
            try:
                counts = migrate(snapshot, target, assets, asset_store=asset_store)
            finally:
                target.close()
            print_summary(counts, len(assets), applied=True)
            print("Il database originale resta intatto. Avvia ora l'app cloud e verifica i dati prima di usarla in gara.")
            return 0
    except MigrationError as exc:
        print(f"Interrotto: {exc}")
    except Exception as exc:
        # Database/API exceptions can contain passwords, tokens, or row values.
        # Keep diagnostics deliberately sparse in a command users may screenshot.
        print(f"Interrotto ({type(exc).__name__}): controllo o connessione non riusciti. "
              "Verifica dipendenze, Secrets, database e bucket; non condividere le credenziali.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
