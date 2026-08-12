# Obsidian Brain (companion plugin)

A thin Obsidian client for the [obsidian-brain](../README.md) semantic search server. It
leans into the one thing a browser tab can't do: **query the brain using the active note as
live context.**

- **Related notes panel** — a side panel (right leaf) that re-queries the brain against the
  note you're currently looking at (even unsaved text), debounced on every note switch.
  Read-only: it never writes into your notes. This complements, not duplicates, the nightly
  `moc_linker` `## Related Notes` block — that one is the durable, offline artifact; this
  panel is the live lens, and it also covers brand-new notes the nightly job hasn't seen yet.
- **"Ask the brain" command** — command palette entry, opens a query box; clicking a result
  inserts a `[[wikilink]]` at your cursor (or opens the note if no editor is active).
- **"Refresh brain index" command** — triggers the server's incremental index rebuild.
- **Status bar health pill** — pings the server every 60s so you can see at a glance whether
  it's reachable.

It talks **plain REST** to the server (never MCP/SSE) using Obsidian's `requestUrl`, which is
CORS-free and works on both desktop and mobile.

## Install via BRAT

This plugin isn't (yet) on the official community list, so install it as a beta plugin via
[BRAT](https://github.com/TfTHacker/obsidian42-brat):

1. Install **BRAT** from Obsidian's Community Plugins browser, and enable it.
2. Open the command palette → **BRAT: Add a beta plugin for testing**.
3. Enter this repository's URL (or `owner/repo` if you've pushed it to GitHub) and confirm.
   BRAT pulls `manifest.json` + `main.js` + `styles.css` from the latest tagged release.
4. Enable **Obsidian Brain** in Settings → Community plugins.

If you're iterating locally instead (no GitHub release yet), copy this plugin's built files
into your vault manually:

```bash
mkdir -p /path/to/your/vault/.obsidian/plugins/obsidian-brain
cp manifest.json main.js styles.css /path/to/your/vault/.obsidian/plugins/obsidian-brain/
```

Then reload Obsidian (or use the "Reload app without saving" command) and enable the plugin.

## Point it at the server

Open Settings → **Obsidian Brain** and set:

| Setting | Meaning |
|---|---|
| Server base URL | e.g. `http://localhost:8053`. No trailing slash needed. |
| Bearer token | Matches the server's `BRAIN_AUTH_TOKEN`. Leave blank only if the server has no token set. |
| Results per query (top_k) | How many distinct notes come back per search / related-notes query. |
| Auto-update related notes | Toggle the panel's automatic re-query on note switch. |
| Debounce (ms) | Delay after switching notes before the panel re-queries. |

**The token is stored in plaintext** in this plugin's `data.json` inside
`.obsidian/plugins/obsidian-brain/`, which most sync setups (including Obsidian LiveSync)
replicate to every device. Use a dedicated token for this plugin, not a secret you reuse
elsewhere — see the server's `deploy/README.md` "Security" section for how the container's
`BRAIN_AUTH_TOKEN` is generated and rotated.

## Reaching a server that isn't on this machine

The deployed server publishes to **loopback only** (`127.0.0.1:8053` on the host, never the
LAN) precisely because its tools read *and write* the vault — see `deploy/README.md`. That
means:

- **Same machine as the server:** `http://localhost:8053` just works.
- **Anywhere else (another desktop, a phone):** you need an SSH port-forward (or a reverse
  proxy / VPN) that reproduces loopback access on your side, e.g.:

  ```bash
  ssh -L 8053:localhost:8053 user@your-server-host
  ```

  Then point the plugin's "Server base URL" at `http://localhost:8053` on the *client*
  machine — the tunnel makes the remote loopback port look local. For a phone or any
  situation an ad-hoc SSH tunnel can't reach, prefer a proper reverse proxy (SWAG/Authelia)
  terminating TLS, or a VPN such as Tailscale, rather than publishing the port directly to
  the LAN.
- Plain `http://` + a bearer header is sniffable on an untrusted network; prefer HTTPS via a
  reverse proxy, or keep it inside a VPN, once you're off a trusted LAN.

## Development

```bash
npm install
npm run dev      # esbuild watch mode: main.ts -> main.js
npm run build    # tsc --noEmit type-check, then a one-shot production esbuild
```

Standard `obsidian-sample-plugin` layout: `manifest.json`, `main.ts` → `main.js` (esbuild,
CommonJS, `obsidian` external), `versions.json`, `styles.css`, TypeScript in strict mode.

## Scope / non-goals (v1)

- No write-back from the panel or ask modal beyond inserting a `[[link]]` in your own note —
  it never edits vault notes itself (`append_insight` / `complete_task` from the editor are a
  possible v2).
- No auto-refresh on save: `POST /refresh` still does a whole-vault incremental scan, which is
  too heavy to fire on every keystroke/save. Use the manual "Refresh brain index" command.
