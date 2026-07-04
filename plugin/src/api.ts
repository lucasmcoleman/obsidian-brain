import { requestUrl, RequestUrlParam } from "obsidian";
import type { BrainPluginSettings } from "./settings";

/** One retrieval hit, matching GET /ui/api/search's {"results": [...]} shape. */
export interface SearchResult {
	text: string;
	note_path: string;
	abs_path: string;
	score: number;
}

interface SearchResponse {
	results: SearchResult[];
}

/** Thrown for every HTTP/network failure so callers can show `message` directly. */
export class BrainApiError extends Error {
	readonly status?: number;

	constructor(message: string, status?: number) {
		super(message);
		this.name = "BrainApiError";
		this.status = status;
	}
}

/**
 * Thin wrapper over Obsidian's requestUrl for the obsidian-brain REST surface.
 * ALWAYS uses requestUrl (never fetch/XHR/node http) so it works on mobile and
 * is exempt from CORS. Every call sets throw:false and branches on status —
 * requestUrl can still reject the promise on a genuine network failure (host
 * unreachable, DNS failure, etc.), so every method is wrapped in try/catch and
 * normalizes both failure modes into a BrainApiError with a readable message.
 */
export class BrainClient {
	constructor(private readonly getSettings: () => BrainPluginSettings) {}

	private baseUrl(): string {
		return this.getSettings().baseUrl.trim().replace(/\/+$/, "");
	}

	private authHeaders(): Record<string, string> {
		const token = this.getSettings().token.trim();
		return token ? { Authorization: `Bearer ${token}` } : {};
	}

	private async request(params: RequestUrlParam): Promise<unknown> {
		let res;
		try {
			res = await requestUrl({ ...params, throw: false });
		} catch (e) {
			throw new BrainApiError(
				`Could not reach the brain server at ${this.baseUrl()} (${
					e instanceof Error ? e.message : String(e)
				}).`,
			);
		}
		if (res.status === 401) {
			throw new BrainApiError(
				"Unauthorized (401) — check the bearer token in Obsidian Brain settings.",
				401,
			);
		}
		if (res.status < 200 || res.status >= 300) {
			throw new BrainApiError(`Brain server returned HTTP ${res.status}.`, res.status);
		}
		try {
			return res.json;
		} catch {
			return undefined;
		}
	}

	/** GET /ui/api/search?q=<text>&k=<n> — shared by the search command and the related panel. */
	async search(query: string, topK: number): Promise<SearchResult[]> {
		const k = Math.max(1, Math.floor(topK) || 5);
		const url = `${this.baseUrl()}/ui/api/search?q=${encodeURIComponent(query)}&k=${k}`;
		const json = await this.request({ url, method: "GET", headers: this.authHeaders() });
		const data = json as SearchResponse | undefined;
		return data && Array.isArray(data.results) ? data.results : [];
	}

	/** POST /refresh — triggers build_index(force=False); returns its JSON result verbatim. */
	async refresh(): Promise<unknown> {
		const url = `${this.baseUrl()}/refresh`;
		return this.request({
			url,
			method: "POST",
			headers: { ...this.authHeaders(), "content-type": "application/json" },
			body: "{}",
		});
	}

	/** GET /health — public, unauthenticated liveness probe. Never throws; returns false on any failure. */
	async health(): Promise<boolean> {
		const url = `${this.baseUrl()}/health`;
		try {
			const res = await requestUrl({ url, method: "GET", throw: false });
			return res.status >= 200 && res.status < 300;
		} catch {
			return false;
		}
	}
}
