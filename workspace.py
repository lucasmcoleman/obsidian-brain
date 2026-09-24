"""Source-grounded director workspace. Markdown records are authoritative;
SQLite/FTS is a disposable projection. No model is required for intake or review.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from safe_paths import atomic_write_bytes, is_scannable_md, resolve_in_vault
from tasks import TASK_RE, _code_fence_mask

RECORD_FOLDER = "Brain Workspace/Records"
KINDS = {"project", "client", "person", "commitment", "decision", "issue"}
STATES = {"open", "waiting", "blocked", "done", "cancelled", "active", "archived"}
_LOCK = threading.RLock()
_LOCAL = threading.local()
_RECORD = re.compile(r"<!-- brain-record:begin -->\s*```json\s*(.*?)\s*```\s*<!-- brain-record:end -->", re.S)


class WorkspaceError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise WorkspaceError("Use a complete date in YYYY-MM-DD format.")


def _metadata(text):
    """Read only flat scalar/list frontmatter. Never execute YAML constructors."""
    result = {}
    lines = text.splitlines()
    end = 0
    first = next((line for line in lines[1:] if line.strip()), "")
    if lines and lines[0].strip() == "---" and re.match(r"^[\w-]+:\s*", first):
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                end = i + 1
                break
            match = re.match(r"^([\w-]+):\s*(.*?)\s*$", line)
            if match:
                key, value = match.groups()
                if value.startswith("[") and value.endswith("]") and not value.startswith("[["):
                    result[key] = [v.strip().strip("\"'") for v in value[1:-1].split(",") if v.strip()]
                else:
                    result[key] = value.strip("\"'")
    return result, end


def _masked_lines(text):
    lines = text.splitlines()
    _, fm_end = _metadata(text)
    generated = False
    result = []
    for i, line in enumerate(lines):
        if re.search(r"<!--\s*(moc-linker(?::[\w-]+)?|ledger-auto|truth-review):begin", line):
            generated = True
        hidden = generated or i < fm_end
        if generated and re.search(r"<!--\s*(moc-linker(?::[\w-]+)?|ledger-auto|truth-review):end", line):
            generated = False
        result.append("" if hidden else line)
    return result


def _task_title(text):
    text = re.sub(r"\s*✅\s*\d{4}-\d{2}-\d{2}", "", text)
    return text.strip()


class Workspace:
    def __init__(self, vault, cache_dir=None):
        self.vault = Path(vault).resolve()
        self.cache_dir = Path(cache_dir) if cache_dir else self.vault / "_brain"
        self.db_path = self.cache_dir / "workspace.sqlite3"
        self.record_dir = self.vault / RECORD_FOLDER

    @contextmanager
    def _lock(self):
        if not self.vault.is_dir():
            raise WorkspaceError("The vault is unavailable. No source scan was performed.", 503)
        with _LOCK:
            held = getattr(_LOCAL, "held", set())
            key = str(self.cache_dir.resolve())
            if key in held:
                yield
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with (self.cache_dir / ".workspace.lock").open("a") as handle:
                try:
                    import fcntl
                except ImportError:
                    fcntl = None
                if fcntl:
                    fcntl.flock(handle, fcntl.LOCK_EX)
                _LOCAL.held = held | {key}
                try:
                    yield
                finally:
                    _LOCAL.held = held
                    if fcntl:
                        fcntl.flock(handle, fcntl.LOCK_UN)

    @contextmanager
    def _db(self):
        if not self.vault.is_dir():
            raise WorkspaceError("The vault is unavailable.", 503)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, path TEXT UNIQUE,
                    revision TEXT, data TEXT NOT NULL, content TEXT NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(id UNINDEXED, title, content);
                CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sections (source_id TEXT, revision TEXT, part INTEGER,
                    state TEXT NOT NULL, detail TEXT, PRIMARY KEY(source_id,revision,part));
            """)
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _registry_path(self):
        path = self.vault / "Brain Workspace" / "Sources.md"
        if not path.resolve().is_relative_to(self.vault):
            raise WorkspaceError("Source registry points outside the vault.", 503)
        return path

    def _registry(self):
        registry = self._registry_path()
        if not registry.exists():
            return {}
        try:
            match = re.search(r"```json\n(.*?)\n```", registry.read_text(), re.S)
            entries = json.loads(match.group(1))
            if not isinstance(entries, dict) or any(not isinstance(v, dict) or not re.fullmatch(r"[a-f0-9]{24}", str(v.get("id"))) or not isinstance(v.get("revision"), str) for v in entries.values()):
                raise ValueError("invalid identities")
            return entries
        except (OSError, ValueError, AttributeError) as exc:
            raise WorkspaceError("The durable source registry needs repair; scan cancelled.", 503) from exc

    def _allowed(self, path):
        rel = path.relative_to(self.vault)
        return (not str(rel).startswith("Brain Workspace/")
                and rel.name != "truth-review-queue.md"
                and is_scannable_md(rel, include_entities=True, brain_top_level_only=True))

    def source(self, note_path):
        if not isinstance(note_path, str):
            raise WorkspaceError("A source path is required.")
        try:
            path = resolve_in_vault(note_path, str(self.vault))
            if not path.is_file() or not self._allowed(path):
                raise WorkspaceError("This note is unavailable or excluded from workspace sources.", 404)
            raw = path.read_text(encoding="utf-8")
        except WorkspaceError:
            raise
        except (OSError, ValueError, UnicodeError) as exc:
            raise WorkspaceError("The source note cannot be read within this vault.", 404) from exc
        if len(raw) > 2_000_000:
            raise WorkspaceError("This source exceeds the 2 MB reading limit.", 413)
        meta, _ = _metadata(raw)
        lines = _masked_lines(raw)
        body = "\n".join(lines).strip()
        event = meta.get("event_date") or meta.get("meeting_date") or meta.get("date")
        if not event:
            match = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
            event = match.group(1) if match else None
        try:
            event = _day(str(event)[:10]) if event else None
        except WorkspaceError:
            event = None
        relevant_meta = {k: meta[k] for k in ("event_date", "meeting_date", "date", "project", "client", "owner", "type", "source_type", "review_status", "superseded_by") if k in meta}
        relevant_meta["resolved_event_date"] = event
        revision = _hash(json.dumps(relevant_meta, sort_keys=True) + "\n" + body)
        rel = path.relative_to(self.vault).as_posix()
        identity = meta.get("scriberr_job_id") or meta.get("source_id") or rel
        title = meta.get("title") or next((s.lstrip("# ") for s in lines if s.startswith("# ")), path.stem)
        source_id = self._registry().get(rel, {}).get("id") or _hash(str(identity))[:24]
        return {"id": source_id, "note_path": rel, "title": title,
                "revision": revision, "raw_revision": _hash(raw), "event_date": event,
                "source_type": meta.get("source_type") or ("transcript" if meta.get("scriberr_job_id") else "unknown"),
                "review_status": meta.get("review_status") or "unreviewed",
                "superseded_by": meta.get("superseded_by"), "metadata": meta,
                "content": raw, "body": body, "lines": lines,
                "total_lines": len(lines), "availability": "available"}

    def _read_records(self):
        records = {}
        for path in sorted(self.record_dir.glob("*.md")):
            try:
                if not path.resolve().is_relative_to(self.vault):
                    raise ValueError("external record")
                match = _RECORD.search(path.read_text(encoding="utf-8"))
                record = json.loads(match.group(1)) if match else None
                if not isinstance(record, dict) or record.get("id") != path.stem or record.get("kind") not in KINDS:
                    raise ValueError("invalid record")
                records[record["id"]] = record
            except (OSError, ValueError, AttributeError) as exc:
                raise WorkspaceError(f"Workspace record {path.name} needs repair; nothing was overwritten.", 503) from exc
        return records

    def _save(self, record):
        if not re.fullmatch(r"[a-f0-9]{24}", record["id"]):
            raise WorkspaceError("Invalid record identifier.")
        if not self.record_dir.resolve().is_relative_to(self.vault):
            raise WorkspaceError("Record folder points outside the vault.")
        self.record_dir.mkdir(parents=True, exist_ok=True)
        display_title = record['title'].replace('<', '&lt;').replace('>', '&gt;').replace('\n', ' ')
        payload = json.dumps(record, ensure_ascii=False, indent=2).replace('<', '\\u003c')
        text = (f"---\ntype: brain-record\nrecord_id: {record['id']}\nkind: {record['kind']}\n---\n\n"
                f"# {display_title}\n\n"
                f"{record['review'].capitalize()} · {record['status']}\n\n"
                "This record and its history are the durable source for the director workspace.\n\n"
                "<!-- brain-record:begin -->\n```json\n" + payload
                + "\n```\n<!-- brain-record:end -->\n")
        atomic_write_bytes(self.record_dir / (record["id"] + ".md"), text.encode("utf-8"))

    def _new(self, identity, kind, title, evidence, **fields):
        return {"id": _hash(identity)[:24], "kind": kind, "title": title[:1000],
                "review": "pending", "status": "open" if kind in {"commitment", "issue", "decision"} else "active",
                "project": None, "client": None, "owner": None, "due": None, "due_text": None,
                "note": "", "aliases": [], "evidence": [evidence] if evidence else [],
                "source_changed": False, "version": 1, "created_at": _now(), "updated_at": _now(),
                "confirmed_at": None, "acknowledged_until": None, "deferred_until": None,
                "history": [], **fields}

    def _evidence(self, source, quote, line):
        return {"source_id": source["id"], "note_path": source["note_path"],
                "revision": source["revision"], "raw_revision": source["raw_revision"], "event_date": source["event_date"],
                "line_start": line, "line_end": line + quote.count("\n"), "quote": quote}

    def sync(self):
        """Scan every eligible source. Imported observations always await review.

        No source note is modified, and old model-generated ledger sections are
        masked. Accepted/rejected records survive cache deletion and re-import.
        """
        with self._lock():
            records = self._read_records()
            with self._db() as db:
                previous = {row["path"]: {"id": row["id"], "revision": row["revision"]} for row in db.execute("SELECT id,path,revision FROM sources")}
                registry = self._registry_path()
                previous.update(self._registry())
                # Recover rename mappings even when SQLite was deleted.
                for record in records.values():
                    for evidence in record.get("evidence", []):
                        previous.setdefault(evidence["note_path"], {"id": evidence["source_id"], "revision": evidence["revision"]})
                sources, skipped = [], []
                for path in sorted(self.vault.rglob("*.md")):
                    if not self._allowed(path):
                        continue
                    try:
                        source = self.source(path.relative_to(self.vault).as_posix())
                    except WorkspaceError:
                        skipped.append(path.relative_to(self.vault).as_posix())
                        continue
                    sources.append(source)
                paths = {s["note_path"] for s in sources}
                missing = [(path, entry) for path, entry in previous.items() if path not in paths]
                new_counts = Counter(s["revision"] for s in sources if s["note_path"] not in previous)
                old_counts = Counter(entry["revision"] for _, entry in missing)
                used_ids = set()
                for source in sources:
                    if source["note_path"] in previous:
                        source["id"] = previous[source["note_path"]]["id"]
                    elif new_counts[source["revision"]] == old_counts[source["revision"]] == 1:
                        source["id"] = next(entry["id"] for _, entry in missing if entry["revision"] == source["revision"])
                    if source["id"] in used_ids:
                        source["id"] = _hash(source["note_path"])[:24]
                    used_ids.add(source["id"])
                created = 0
                for source in sources:
                    meta = source["metadata"]
                    project = None
                    kind = {"project-note": "project", "project": "project", "client": "client", "person": "person"}.get(meta.get("type"))
                    if kind:
                        record = self._new(source["id"] + ":entity:" + kind, kind, source["title"],
                                           self._evidence(source, source["title"] if source["title"] in source["content"] else source["body"][:200], 1))
                        record["client"] = meta.get("client") or None
                        record["owner"] = meta.get("owner") or None
                        project = record["id"] if kind == "project" else None
                        if record["id"] not in records:
                            records[record["id"]] = record
                            self._save(record)
                            created += 1
                    seen = {}
                    mask = _code_fence_mask(source["lines"])
                    for i, line in enumerate(source["lines"]):
                        match = TASK_RE.match(line) if not mask[i] else None
                        if not match:
                            continue
                        title = _task_title(match.group("text"))
                        key = re.sub(r"\s+", " ", title).casefold()
                        seen[key] = seen.get(key, 0) + 1
                        identity = source["id"] + ":task:" + key + ":" + str(seen[key])
                        ev = self._evidence(source, line, i + 1)
                        explicit_due = re.search(r"📅\s*(\d{4}-\d{2}-\d{2})", title)
                        try:
                            due = _day(explicit_due.group(1)) if explicit_due else None
                        except WorkspaceError:
                            due = None
                        record = self._new(identity, "commitment", title, ev, project=project,
                                           due=due, status="done" if match.group("mark").lower() == "x" else "open",
                                           origin="checkbox")
                        existing = records.get(record["id"])
                        if not existing:
                            records[record["id"]] = record
                            self._save(record)
                            created += 1
                        elif existing["evidence"][0]["revision"] != source["revision"] and existing.get("reviewed_source_revision") != source["revision"]:
                            if existing.get("latest_evidence") != ev:
                                existing.update(source_changed=True, latest_evidence=ev,
                                                observed_status=record["status"], version=existing["version"] + 1)
                                self._save(existing)
                # Every accepted fact retains its original evidence. Missing,
                # removed and reworded evidence must be visible too, not only
                # checkboxes whose text happens to still match.
                by_id = {s["id"]: s for s in sources}
                for record in records.values():
                    if not record.get("evidence"):
                        continue
                    ev = record["evidence"][0]
                    source = by_id.get(ev["source_id"])
                    current_revision = source["revision"] if source else "missing"
                    if (source is None or source["revision"] != ev["revision"]) and record.get("reviewed_source_revision") != current_revision:
                        changed = not record.get("source_changed")
                        record["source_changed"] = True
                        if source and ev["quote"] in source["content"]:
                            pos = source["content"].find(ev["quote"])
                            latest = self._evidence(source, ev["quote"], source["content"][:pos].count("\n") + 1)
                            if record.get("latest_evidence") != latest:
                                record["latest_evidence"] = latest
                                changed = True
                        elif record.get("latest_evidence") and (not source or record["latest_evidence"]["revision"] != source["revision"]):
                            record.pop("latest_evidence", None)
                            changed = True
                        if changed:
                            record["version"] += 1
                            self._save(record)
                db.execute("DELETE FROM sources")
                db.execute("DELETE FROM source_fts")
                for source in sources:
                    data = {k: v for k, v in source.items() if k not in {"content", "body", "lines"}}
                    db.execute("INSERT INTO sources VALUES (?,?,?,?,?)", (source["id"], source["note_path"], source["revision"], json.dumps(data), source["content"]))
                    db.execute("INSERT INTO source_fts VALUES (?,?,?)", (source["id"], source["title"], source["body"]))
                self._project_records(db, records)
                previous.update({s["note_path"]: {"id": s["id"], "revision": s["revision"]} for s in sources})
                registry.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(registry, ("# Workspace source identities\n\nRenames and source identities survive database rebuilds.\n\n```json\n" + json.dumps(previous, indent=2) + "\n```\n").encode())
                dates = [s["event_date"] for s in sources if s["event_date"]]
                coverage = {"state": "partial" if skipped else "ready", "scanned_at": _now(),
                            "sources": len(sources), "skipped": skipped, "latest_source_date": max(dates) if dates else None,
                            "undated_sources": sum(not s["event_date"] for s in sources), "created": created}
                db.execute("INSERT OR REPLACE INTO meta VALUES ('coverage',?)", (json.dumps(coverage),))
            return coverage

    def _project_records(self, db, records):
        db.execute("DELETE FROM records")
        db.executemany("INSERT INTO records VALUES (?,?)", [(k, json.dumps(v)) for k, v in records.items()])

    def records(self, kind=None, review=None):
        with self._lock():
            records = self._read_records()
        return sorted((r for r in records.values() if (not kind or r["kind"] == kind) and (not review or r["review"] == review)),
                      key=lambda r: (r["kind"] != "project", r["updated_at"], r["title"]), reverse=False)

    def record(self, record_id):
        if not isinstance(record_id, str):
            raise WorkspaceError("A record ID is required.")
        with self._lock():
            record = self._read_records().get(record_id)
        if not record:
            raise WorkspaceError("Record not found.", 404)
        return record

    def sources(self):
        with self._db() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM sources ORDER BY path")]

    def coverage(self):
        with self._db() as db:
            row = db.execute("SELECT value FROM meta WHERE key='coverage'").fetchone()
            result = json.loads(row[0]) if row else {"state": "not_scanned", "sources": 0, "skipped": [], "latest_source_date": None}
            jobs = list(db.execute("SELECT sections.state,count(*) AS count FROM sections JOIN sources ON sources.id=sections.source_id AND sources.revision=sections.revision GROUP BY sections.state"))
            result["processing"] = {r["state"]: r["count"] for r in jobs}
        return result

    def source_for_record(self, record_id):
        record = self.record(record_id)
        if not record.get("evidence"):
            return {"availability": "manual", "evidence": None}
        evidence = record["evidence"][0]
        try:
            source = self.source(evidence["note_path"])
        except WorkspaceError:
            source = None
            for candidate in self.sources():
                if candidate["id"] == evidence["source_id"] or candidate["revision"] == evidence["revision"]:
                    try:
                        source = self.source(candidate["note_path"])
                    except WorkspaceError:
                        pass
                    break
            if source is None:
                return {"availability": "missing", "evidence": evidence}
        source["evidence"] = evidence
        source["changed"] = source["revision"] != evidence["revision"]
        quote = evidence["quote"]
        if quote and source["content"].count(quote) == 1:
            line = source["content"][:source["content"].index(quote)].count("\n") + 1
            source["evidence"] = {**evidence, "line_start": line, "line_end": line + quote.count("\n")}
        return source

    def change(self, record_id, action, version, fields=None, *, _new_record=None):
        if not isinstance(record_id, str) or not isinstance(action, str) or type(version) is not int:
            raise WorkspaceError("A record ID, action and numeric version are required.")
        fields = {} if fields is None else fields
        if not isinstance(fields, dict):
            raise WorkspaceError("Fields must be an object.")
        allowed = {"title", "project", "client", "owner", "due", "due_text", "status", "note", "aliases", "until"}
        if set(fields) - allowed:
            raise WorkspaceError("An unsupported record field was supplied.")
        with self._lock():
            records = self._read_records()
            if _new_record is not None:
                records[record_id] = _new_record
            r = records.get(record_id)
            if not r:
                raise WorkspaceError("Record not found.", 404)
            if version != r["version"]:
                raise WorkspaceError("This record changed. Reload it before saving.", 409)
            before = {k: v for k, v in r.items() if k != "history"}
            if action not in {"accept", "accept_changes", "keep_current", "reject", "update", "resolve", "acknowledge", "defer", "reopen", "refresh"}:
                raise WorkspaceError("Unknown action.")
            if action == "refresh":
                if not r.get("latest_evidence"):
                    raise WorkspaceError("No replacement passage was found. Inspect the preserved evidence and update or resolve the accepted record explicitly.", 409)
                return r  # viewing proposed evidence never changes accepted state
            if action == "accept_changes":
                latest = r.get("latest_evidence")
                if not latest:
                    raise WorkspaceError("Scan sources to locate updated evidence first.", 409)
                source = self.source(latest["note_path"])
                if source["revision"] != latest["revision"] or latest["quote"] not in source["content"]:
                    raise WorkspaceError("Evidence changed again. Scan sources and review the latest version.", 409)
                r["evidence"] = [r.pop("latest_evidence")]
                r["source_changed"] = False
                r.pop("observed_status", None)
                r["review"] = "accepted"
                r["confirmed_at"] = _now()
            elif action == "accept":
                for ev in r["evidence"]:
                    source = self.source_for_record(record_id)
                    if source["availability"] == "missing" or source.get("changed"):
                        raise WorkspaceError("The evidence changed or is missing. Refresh and review it before accepting.", 409)
                r["review"] = "accepted"
                r["confirmed_at"] = _now()
            elif action == "keep_current":
                if r["review"] != "accepted":
                    raise WorkspaceError("Only accepted records can keep their current position.")
                source = self.source_for_record(record_id)
                r["reviewed_source_revision"] = source.get("revision", "missing")
                r["source_changed"] = False
                r.pop("latest_evidence", None)
                r.pop("observed_status", None)
                r["confirmed_at"] = _now()
            elif action == "reject":
                r["review"] = "rejected"
            elif action == "resolve":
                if r["review"] != "accepted":
                    raise WorkspaceError("Accept this record before resolving it.")
                r["status"] = "done"
            elif action == "reopen":
                r["status"] = "open"
                r["acknowledged_until"] = None
                r["deferred_until"] = None
            elif action == "acknowledge":
                r["acknowledged_until"] = (date.today() + timedelta(days=1)).isoformat()
            elif action == "defer":
                until = _day(fields.get("until"))
                if not until or until <= date.today().isoformat():
                    raise WorkspaceError("Choose a future review date.")
                r["deferred_until"] = until
            for key, value in fields.items():
                if key == "until":
                    continue
                if key == "aliases":
                    if not isinstance(value, list) or len(value) > 30 or any(not isinstance(x, str) or len(x) > 200 for x in value):
                        raise WorkspaceError("Aliases must be a short list of names.")
                elif value is not None and (not isinstance(value, str) or len(value) > (4000 if key == "note" else 1000)):
                    raise WorkspaceError("Record fields must contain bounded text.")
                if key == "due":
                    value = _day(value)
                if key == "status" and value not in STATES:
                    raise WorkspaceError("Invalid record status.")
                if key == "title" and not (value or "").strip():
                    raise WorkspaceError("A title is required.")
                if key == "project" and value and (value not in records or records[value]["kind"] != "project"):
                    raise WorkspaceError("Choose an existing project.")
                r[key] = value or None if key in {"project", "client", "owner", "due", "due_text"} else value
            if action in {"accept", "accept_changes", "update", "resolve", "reopen"}:
                r["acknowledged_until"] = None
                r["deferred_until"] = None
                if r["review"] == "accepted":
                    r["confirmed_at"] = _now()
            r["version"] += 1
            r["updated_at"] = _now()
            r["history"].append({"at": _now(), "action": action, "before": before, "note": fields.get("note", "")})
            self._save(r)
            with self._db() as db:
                self._project_records(db, records)
            return r

    def create(self, kind, title, fields=None):
        if not isinstance(kind, str) or kind not in KINDS or not isinstance(title, str) or not title.strip() or len(title) > 1000:
            raise WorkspaceError("Choose a record type and provide a title.")
        with self._lock():
            import uuid
            record = self._new(uuid.uuid4().hex, kind, title.strip(), None, origin="manual")
            # Validate before the first write, using the same review contract.
            return self.change(record["id"], "accept", 1, fields, _new_record=record)

    def attention(self, today=None):
        today = today or date.today()
        items = []
        records = self.records()
        projects = {r["id"]: r for r in records if r["kind"] == "project"}
        for r in records:
            if r["review"] != "accepted" or r["status"] in {"done", "cancelled", "archived"}:
                continue
            if any(r.get(k) and r[k] > today.isoformat() for k in ("deferred_until", "acknowledged_until")):
                continue
            reasons, priority = [], 0
            if r.get("source_changed"):
                reasons.append("Source changed; review the current evidence")
                priority = 90
            if r["kind"] in {"commitment", "issue", "decision"}:
                if r.get("due"):
                    days = (date.fromisoformat(r["due"]) - today).days
                    if days < 0:
                        reasons.append(f"Accepted deadline passed {abs(days)} days ago")
                        priority = max(priority, 100)
                    elif days <= 7:
                        reasons.append("Due today" if days == 0 else f"Due in {days} days")
                        priority = max(priority, 80 - days)
                if r["status"] in {"waiting", "blocked"}:
                    reasons.append("Waiting on a response" if r["status"] == "waiting" else "Confirmed blocker")
                    priority = max(priority, 85)
                if r["kind"] == "decision" and r["status"] == "open":
                    reasons.append("Decision needs follow-through")
                    priority = max(priority, 60)
                if not r.get("owner"):
                    reasons.append("Owner unconfirmed")
                    priority = max(priority, 40)
            elif r["kind"] == "project":
                confirmed = (r.get("confirmed_at") or r["created_at"])[:10]
                if (today - date.fromisoformat(confirmed)).days >= 14:
                    reasons.append("Project status has not been confirmed in 14 days")
                    priority = max(priority, 50)
            if reasons:
                project = projects.get(r.get("project"))
                items.append({**r, "reasons": reasons, "priority": priority,
                              "project_title": project["title"] if project else None})
        items.sort(key=lambda r: (-r["priority"], r.get("due") or "9999", r["title"]))
        return {"items": items, "coverage": self.coverage(), "generated_at": _now(),
                "counts": {"attention": len(items), "pending": sum(r["review"] == "pending" or (r["review"] == "accepted" and r.get("source_changed")) for r in records),
                           "projects": sum(r["review"] == "accepted" for r in projects.values()),
                           "commitments": sum(r["kind"] == "commitment" and r["review"] == "accepted" and r["status"] not in {"done", "cancelled"} for r in records)}}

    def brief(self, record_id):
        entity = self.record(record_id)
        rows = self.records()
        names = {entity["title"].casefold(), *(a.casefold() for a in entity.get("aliases", []))}
        def related(r):
            if entity["kind"] == "project":
                return r.get("project") == record_id
            field = "client" if entity["kind"] == "client" else "owner"
            if str(r.get(field) or "").casefold() in names:
                return True
            return entity["kind"] == "client" and any(p["id"] == r.get("project") and p["kind"] == "project" and str(p.get("client") or "").casefold() in names for p in rows)
        related_rows = [r for r in rows if related(r)]
        return {"entity": entity, "records": related_rows,
                "accepted": [r for r in related_rows if r["review"] == "accepted"],
                "pending": [r for r in related_rows if r["review"] == "pending"],
                "coverage": self.coverage()}

    def search(self, query, limit=30):
        query = str(query or "")[:4000]
        tokens = re.findall(r"[\w-]+", query, re.UNICODE)
        if not tokens:
            return {"results": [], "mode": "lexical", "coverage": self.coverage()}
        expression = " AND ".join('"' + t.replace('"', '""') + '"' for t in tokens[:30])
        with self._db() as db:
            rows = db.execute("""SELECT s.data, snippet(source_fts,2,'','', ' … ',48) AS excerpt,
                bm25(source_fts) AS rank FROM source_fts JOIN sources s ON s.id=source_fts.id
                WHERE source_fts MATCH ? ORDER BY rank LIMIT ?""", (expression, max(1, min(int(limit), 100)))).fetchall()
        return {"results": [{**json.loads(r["data"]), "text": r["excerpt"]} for r in rows],
                "mode": "lexical", "coverage": self.coverage()}

    def propose(self, note_path, revision, items):
        if not isinstance(items, list) or len(items) > 100:
            raise WorkspaceError("Proposals must be a bounded list.")
        source = self.source(note_path)
        if source["revision"] != revision:
            raise WorkspaceError("The source changed during processing. Rescan it.", 409)
        proposals = []
        for item in items:
            if not isinstance(item, dict) or item.get("kind") not in {"commitment", "decision", "issue"}:
                raise WorkspaceError("Invalid proposal type.")
            title, quote = item.get("title"), item.get("quote")
            if not isinstance(title, str) or not title.strip() or len(title) > 1000:
                raise WorkspaceError("A bounded proposal title is required.")
            if not isinstance(quote, str) or len(quote.strip()) < 10 or len(quote) > 6000 or quote not in source["body"] or quote not in source["content"]:
                raise WorkspaceError("Every proposal needs an exact supporting passage from this source.")
            start = source["content"].find(quote)
            line = source["content"][:start].count("\n") + 1
            ev = self._evidence(source, quote, line)
            identity = source["id"] + ":prose:" + item["kind"] + ":" + quote + ":" + re.sub(r"\s+", " ", title).casefold()
            owner = item.get("owner")
            if owner and (not isinstance(owner, str) or owner not in quote):
                owner = None
            proposals.append(self._new(identity, item["kind"], title, ev, owner=owner,
                                       origin="extraction", due_text=item.get("due_text") if isinstance(item.get("due_text"), str) and item["due_text"] in quote else None))
        with self._lock():
            records = self._read_records()
            for proposal in proposals:
                if proposal["id"] not in records:
                    self._save(proposal)
                    records[proposal["id"]] = proposal
            with self._db() as db:
                self._project_records(db, records)
        return [records[p["id"]] for p in proposals]


if __name__ == "__main__":
    from config import VAULT_PATH
    print(json.dumps(Workspace(VAULT_PATH).sync(), indent=2))
