# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does not currently cut versioned releases; entries accumulate under
[Unreleased] with the date noted per change.

## [Unreleased]

<!-- Simplification pass 2026-08-09: each entry notes the change, reason, files touched, and how it was verified. -->

### Added

- (2026-08-12) `LICENSE` (MIT) — the repo was previously unlicensed, which made
  it legally unusable by third parties. License applied to prepare the repo for
  public release.
- (2026-08-12) `deploy/docker-compose.yml` — a self-contained, scrubbed
  single-service example replacing the previously-documented compose block that
  lived outside the repo (with machine-specific paths).
- (2026-08-09) The web UI's search results now carry a "＋ Insight" button that
  expands an inline form (insight text + optional context) and posts to the
  existing bearer-gated `POST /ui/api/insight` — the endpoint finally has its
  intended UI affordance instead of being an API-only orphan. Success swaps the
  form for "✓ appended"; endpoint errors surface inline. Built with the page's
  existing `textContent`-only rendering, so note-derived text stays inert.
  Verified: a new test pins that the served page references the endpoint (it
  can't silently go orphan again), and a new characterization test covers the
  endpoint's previously-untested happy path — 199 passed.

### Changed

- (2026-08-12) Machine-specific defaults removed from the code: `config.py`'s
  `OBSIDIAN_VAULT_PATH` no longer falls back to a baked-in host path (it now fails
  loudly with "OBSIDIAN_VAULT_PATH must be set…" instead of silently indexing a
  machine-specific path), `LM_BASE_URL`/`DEFAULT_EMBED_ENDPOINT` default to
  `http://localhost:1234/v1`, and `moc_linker.py`'s `--vault` defaults to
  `$OBSIDIAN_VAULT_PATH` (exits 2 if neither is provided). Behavior is unchanged
  wherever the env vars are set (every documented deployment sets them).
  Verified: full suite green after the change.
- (2026-08-12) Repo-wide docs scrub for public release: README.md, CLAUDE.md,
  SKILL.md, deploy/README.md, eval/, tests/conftest.py, mcp_server.py docstrings
  and the OAuth shim spec now use placeholder paths/hosts (e.g.
  `/path/to/vault`, `localhost:1234`, `brain.example.com`) instead of live
  deployment details; first-person and personal-name references were
  genericized.
- (2026-08-12) README.md restructured to be public-facing: a first-time-user
  path (pitch → Quick Start → connect-an-agent) replaces the previous
  changelog-style opening, the dated 2026-07 "human-facing surfaces" callout
  was folded into Features (web UI, Obsidian plugin, provenance-aware
  retrieval, `truth_maintenance.py`), internal-ops asides were dropped (commit
  refs, "(different default than config.py!)" notes, "the deploy compose"
  phrasing), and a License section was added. All environment variables,
  defaults, flags, exit codes, and tool signatures are unchanged. Verified:
  re-read against the pre-rewrite content; no code touched.
- (2026-08-09) The four OAuth public-path strings in `mcp_server.py` are now
  module-level constants used both by the route decorators and by the auth
  middleware's `public_paths` set, instead of being typed twice. Reason: auth
  matches paths by exact equality, so a one-character drift between the two
  copies would silently gate or expose a route with no test catching it. Pure
  refactor — identical paths, identical resulting set. Verified: 167 passed;
  `test_oauth.py` exercises all four routes end-to-end with auth enabled.
- (2026-08-09) Obsidian plugin: the byte-identical note-opening logic in
  `askModal.ts` and `relatedView.ts` is now one shared `openVaultNote()` in
  `plugin/src/vaultNav.ts` (TFile lookup with `openLinkText` fallback,
  unchanged). `main.js` rebuilt from source. Verified: `npm run build` exits 0
  with `noUnusedLocals` enabled; no TS test harness exists, so a manual
  click-check in Obsidian (Ask modal result + Related panel link) is the
  remaining verification.
- (2026-08-09) `task_sweep.py` no longer carries verbatim copies of
  `ledger_update.gather_recent_notes`/`build_context`; the originals gained
  trailing defaulted parameters (`note_chars`, `max_chars`) and task_sweep
  calls them with its existing CLI-flag values. Defaults and behavior are
  unchanged for both callers. Verified: 167 passed (`test_task_sweep.py`
  end-to-end, `test_ledger.py` direct).
- (2026-08-09) The three copy-pasted managed-block upserters (`moc_linker.
  upsert_managed_block`, `upsert_related_block`, `truth_maintenance.
  upsert_queue_block`) now share one `moc_linker._upsert_block` core; the three
  public names/signatures are unchanged. The two divergent first-write behaviors
  (title-heading vs whitespace-preserving append), the verbatim refuse-path log
  messages, and the backslash-safe `lambda` replacement form are all preserved
  byte-for-byte. Verified: full suite green (refuse-on-multiple + backslash
  regression tests in `test_moc_linker.py`, idempotency in
  `test_truth_maintenance.py`).
- (2026-08-09) The vault path-exclusion policy duplicated between
  `indexer._is_excluded_path` and `tasks._iter_md_files` is now one shared
  `safe_paths.is_scannable_md(rel, *, include_entities, brain_top_level_only)`.
  The two copies had silently drifted twice (entity notes indexed but not
  task-scanned; `_brain` anchored top-level-only in the indexer but matched at
  any depth in tasks) — both historical semantics are reproduced exactly and now
  pinned by two new characterization tests
  (`test_scan_skips_brain_dir_at_any_depth_including_entities`,
  `test_scan_vault_indexes_nested_brain_dir`); CLAUDE.md's stale claim that both
  scanners share one `_brain` rule was corrected. Verified: 169 passed (167 + 2
  new), new tests written and passed against the pre-refactor code first.
  (A third divergent copy in `moc_linker.should_skip` is deliberately out of
  scope — see Future work.)
- (2026-08-09) `ledger_update.find_json_object` now reuses
  `moc_linker._iter_json_objects` for the balanced-brace JSON scan instead of
  carrying a character-for-character copy of the same state machine. Its own
  semantics are unchanged: `<think>`-only stripping (no fence-stripping), the
  presence-test key preference for `completed`/`new_items`, same return
  contract. Verified: 169 passed (4 tests in `test_ledger.py` pin
  `find_json_object`, 5 in `test_moc_linker.py` pin `extract_json`).
- (2026-08-09) The four copy-pasted chat-model retry loops
  (`moc_linker.classify_note`, `ledger_update.ask_model`,
  `truth_maintenance.judge_contradiction`, `task_sweep.ask_model`) now share
  one `moc_linker.call_chat_json` helper owning the POST, the
  content→reasoning_content fallback parse, and the linear-backoff retry.
  Every verified divergence is preserved at the call sites: the truth judge's
  1.0 backoff (vs 1.5 elsewhere — `backoff` is keyword-required so it can
  never be silently inherited), per-site parsers (`extract_json` keeps fence
  stripping for classify_note), validity checks, sentinels, and stderr
  formats. Verified: 18 new characterization tests written and passed against
  the pre-consolidation code first, then the full suite (188) after.
- (2026-08-09) The duplicated write-disabled guard and JSON body parsing in
  `/ui/api/insight` and `/ui/api/complete` are now two small shared helpers
  (`_writes_enabled_or_503`, `_parse_json_body`); status codes, bodies, and
  each route's own required-field checks are unchanged. A new test mirrors the
  invalid-JSON 400 case for `/insight`, so both routes' shared parsing is
  directly covered. Verified: 188 passed (169 baseline + this test + the 18
  chat-loop characterization tests).
- (2026-08-09) `moc_linker.should_skip` — the third copy of the vault
  path-exclusion policy — now routes through `safe_paths.is_scannable_md`
  (keeping its scanner-specific `MOCs/` skip). **Two small deliberate behavior
  changes for the nightly linker:** (1) ALL dot-directories are now skipped,
  not just `.trash`/`.obsidian` — a `.git/` or `.stversions/` dir inside the
  vault is no longer classified or Related-linked; (2) livesync-log matching
  is now a case-sensitive `livesync_log_` prefix test instead of a
  case-insensitive regex (LiveSync's real files are lowercase). Everything
  else — `_brain` at any depth, no entities carve-out — is unchanged and
  pinned by 9 new tests. Two stale README lines about the scanners' shared
  `_brain` rule corrected. Verified: 197 passed.
- (2026-08-09) `moc_linker._normalize` (L2-normalize an embedding vector)
  renamed to `_l2_normalize`, ending the confusing name collision with
  `ledger_update._normalize` (whitespace-normalize a string) across modules
  that import each other. Internal-only rename; both call sites
  (`moc_linker.cross_link`, `truth_maintenance.main`) updated. Verified: 197
  passed.

### Removed

- (2026-08-12) Internal deployment docs, retired as part of the public-release
  scrub: `AUDIT.md`, `AUDIT_2026-07-01.md`, `AUDIT_2026-07-03.md` (live-infra
  audit narratives with real network/IP details), `RESUME.md` (deployment status
  doc for one machine) and `IMPROVEMENTS.md` (superseded backlog referencing
  internal audit IDs). README's dangling references to `AUDIT_2026-07-03.md`
  were removed with them. Git history preserves the files.
- (2026-08-09) Dead function `brain.get_or_build_index` and seven unused imports
  across `brain.py`, `indexer.py`, `searcher.py`, `consolidate.py`. The function
  had zero callers repo-wide and was a no-op wrapper over `build_index` (which
  already calls `ensure_dirs`); the imports were confirmed unused via AST
  analysis including monkeypatch targets (`brain.VAULT_PATH` is patched by tests
  and was kept). README's module table no longer credits `get_or_build_index`
  as backing `brain_build_index`. Verified: full suite 167 passed before and
  after.
- (2026-08-09) `obsidian_brain.py`, the 316-line pre-FAISS legacy prototype.
  Zero imports anywhere (code, tests, eval, deploy, CI, plugin); it was already
  excluded from the container image, and its JSON index was never
  interchangeable with the production FAISS index. README's legacy-prototype
  table, env-var section, and source-file list were trimmed to match; its
  `.dockerignore` line removed. Git history preserves the file. Verified: suite
  green; `grep -rn obsidian_brain --include='*.py'` returns zero hits.
- (2026-08-09) `obsidian-brain.md`, a stale pre-MCP duplicate of `SKILL.md`
  carrying the only occurrence of an outdated vault path in the repo and usage
  examples (direct python imports, cron-based consolidate) that no longer
  describe the interface. Its `.dockerignore` line and README mention were
  removed with it.

### Fixed

- (2026-08-09) Pre-edit backups in the nightly maintenance scripts no longer
  clobber each other on repeated runs: all six hand-rolled backup-writing sites
  across `moc_linker.py` (tag_notes, cross_link), `truth_maintenance.py`
  (apply_provenance, review-queue), `ledger_update.py`, and `task_sweep.py` now
  go through one generalized `moc_linker.backup_file(path, backup_dir, *,
  stem=None)`. Three sites previously wrote un-timestamped `{stem}.bak.md`
  names, so a rerun overwrote the prior backup — every backup is now
  timestamped (this was recommended by two prior audits, M8). Filenames at the
  already-timestamped sites are unchanged, including task_sweep's flattened
  `path__to__note.md.<stamp>.bak.md` shape pinned by
  `test_apply_flips_evidenced_task_and_backs_up`. Verified: 169 passed; the
  only remaining `.bak.md` construction site is inside the helper.
- (2026-08-09) README's Configuration section contradicted itself on
  `LM_BASE_URL`/`EMBEDDING_MODEL`: the warning heading said "not
  env-configurable" while the body (and `config.py`, and the production compose
  file) say they are env-driven. Heading and the two table rows now correctly
  describe them as env overrides with `config.py` fallbacks; the genuinely
  hardcoded `CHUNK_SIZE`/`CHUNK_OVERLAP`/`TOP_K` rows were left alone.

## Future work

(Nothing currently deferred — the 2026-08-09 audit's deferred items were all
addressed the same day; see the entries above.)
