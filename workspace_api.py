"""Same-origin HTTP and MCP access to the durable director workspace."""
import json
import os
import sqlite3
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from workspace import Workspace, WorkspaceError


def register_workspace(mcp, vault_path):
    work = Workspace(vault_path)
    from workspace_extract import ExtractionJobs
    jobs = ExtractionJobs(work)

    def response(data, status=200):
        return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})

    async def invoke(fn, *args):
        try:
            return response(await run_in_threadpool(fn, *args))
        except WorkspaceError as exc:
            return response({"state": "unavailable" if exc.status == 503 else "error", "detail": str(exc)}, exc.status)
        except (OSError, ValueError, sqlite3.Error):
            return response({"state": "unavailable", "detail": "Workspace data could not be read. Retry or check server logs."}, 503)

    async def body(request):
        if not os.environ.get("BRAIN_AUTH_TOKEN", "").strip():
            raise WorkspaceError("Writes require a configured bearer token.", 503)
        raw = await request.body()
        if len(raw) > 100_000:
            raise WorkspaceError("Request body is too large.", 413)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise WorkspaceError("Request must contain valid JSON.")
        if not isinstance(value, dict):
            raise WorkspaceError("Request must be a JSON object.")
        return value

    @mcp.custom_route("/ui/api/workspace", methods=["GET"])
    async def overview(request):
        return await invoke(work.attention)

    @mcp.custom_route("/ui/api/records", methods=["GET"])
    async def records(request):
        return await invoke(lambda: {"records": work.records(request.query_params.get("kind"), request.query_params.get("review"))})

    @mcp.custom_route("/ui/api/brief", methods=["GET"])
    async def brief(request):
        return await invoke(work.brief, request.query_params.get("id", ""))

    @mcp.custom_route("/ui/api/source", methods=["GET"])
    async def source(request):
        if request.query_params.get("record"):
            return await invoke(work.source_for_record, request.query_params["record"])
        return await invoke(work.source, request.query_params.get("path", ""))

    @mcp.custom_route("/ui/api/sources", methods=["GET"])
    async def sources(request):
        return await invoke(lambda: {"sources": work.sources(), "coverage": work.coverage()})

    @mcp.custom_route("/ui/api/lookup", methods=["GET"])
    async def lookup(request):
        return await invoke(work.search, request.query_params.get("q", ""))

    @mcp.custom_route("/ui/api/sync", methods=["POST"])
    async def sync(request):
        try:
            await body(request)
        except WorkspaceError as exc:
            return response({"detail": str(exc)}, exc.status)
        return await invoke(work.sync)

    @mcp.custom_route("/ui/api/review", methods=["POST"])
    async def review(request):
        try:
            data = await body(request)
        except WorkspaceError as exc:
            return response({"detail": str(exc)}, exc.status)
        return await invoke(work.change, data.get("id"), data.get("action"), data.get("version"), data.get("fields"))

    @mcp.custom_route("/ui/api/records", methods=["POST"])
    async def create(request):
        try:
            data = await body(request)
        except WorkspaceError as exc:
            return response({"detail": str(exc)}, exc.status)
        return await invoke(work.create, data.get("kind"), data.get("title"), data.get("fields"))

    @mcp.custom_route("/ui/api/extract", methods=["POST"])
    async def extract(request):
        try:
            data = await body(request)
        except WorkspaceError as exc:
            return response({"detail": str(exc)}, exc.status)
        return await invoke(jobs.start, data.get("note_path", ""))

    @mcp.custom_route("/ui/api/extraction", methods=["GET"])
    async def extraction_status(request):
        return await invoke(jobs.status, request.query_params.get("id", ""))

    @mcp.tool()
    def brain_attention() -> str:
        """Accepted commitments needing attention, with reasons, sources and coverage.
        Unknown/stale evidence is not proof of no outstanding work. Import with
        brain_workspace_sync, then review proposals before treating them as current.
        """
        return json.dumps(work.attention(), ensure_ascii=False)

    @mcp.tool()
    def brain_workspace_sync() -> str:
        """Read source notes and create pending workspace proposals; never alter source
        checkboxes or automatically accept historical actions. No model is required.
        """
        return json.dumps(work.sync(), ensure_ascii=False)

    @mcp.tool()
    def brain_records(kind: str = "", review: str = "") -> str:
        """List project/client/person/commitment/decision/issue records with stable IDs.
        review can be pending, accepted or rejected. Preserve unknown dates/owners.
        """
        return json.dumps(work.records(kind or None, review or None), ensure_ascii=False)

    @mcp.tool()
    def brain_brief(record_id: str) -> str:
        """Read a project, client or person brief with accepted and pending records."""
        return json.dumps(work.brief(record_id), ensure_ascii=False)

    @mcp.tool()
    def brain_source(note_path: str = "", record_id: str = "") -> str:
        """Read original source evidence. record_id also returns preserved evidence and
        signals when the source changed or disappeared. Do not treat stale as current.
        """
        return json.dumps(work.source_for_record(record_id) if record_id else work.source(note_path), ensure_ascii=False)

    @mcp.tool()
    def brain_lookup(query: str) -> str:
        """Exact lexical source lookup, including project numbers; works without embeddings."""
        return json.dumps(work.search(query), ensure_ascii=False)

    @mcp.tool()
    def brain_review(record_id: str, action: str, version: int, fields: dict | None = None) -> str:
        """Apply a user-authorized review or update using the record's current version.
        Actions: accept, accept_changes, keep_current, reject, update, resolve,
        acknowledge, defer, reopen, refresh. keep_current confirms the accepted
        position after reading changed evidence without changing its provenance.
        Ask about unknown owner/date instead of inventing one. Acknowledge/defer do not
        complete work; resolve records a confirmed resolution without editing sources.
        """
        return json.dumps(work.change(record_id, action, version, fields), ensure_ascii=False)

    @mcp.tool()
    def brain_record_create(kind: str, title: str, fields: dict | None = None) -> str:
        """Create a user-confirmed project, client, person, commitment, decision or issue.
        Use only for information the user authorized recording; unknown fields stay null.
        """
        return json.dumps(work.create(kind, title, fields), ensure_ascii=False)

    @mcp.tool()
    def brain_extract_meeting(note_path: str) -> str:
        """Process every source section with the configured local chat model into pending,
        quoted proposals. Failed sections remain visible and retryable; nothing is accepted.
        """
        return json.dumps(jobs.start(note_path), ensure_ascii=False)

    @mcp.tool()
    def brain_extraction_status(job_id: str = "") -> str:
        """Poll background extraction progress, or list recent jobs. Failed/interrupted
        work can be resumed with brain_extract_meeting; prior successes are retained.
        """
        return json.dumps(jobs.status(job_id), ensure_ascii=False)

    return work
