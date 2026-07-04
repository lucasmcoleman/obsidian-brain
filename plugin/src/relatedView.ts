import { ItemView, TFile, WorkspaceLeaf } from "obsidian";
import type BrainPlugin from "../main";
import type { SearchResult } from "./api";

export const RELATED_VIEW_TYPE = "brain-related-view";

/**
 * Live related-notes side panel: re-queries GET /ui/api/search using the active
 * note's title + text as the query, debounced on every active-leaf-change /
 * file-open. Read-only — it renders clickable results in the panel only and
 * never writes anything back into the note (that durable job belongs to the
 * nightly moc_linker's "## Related Notes" block; this is the live lens).
 */
export class RelatedNotesView extends ItemView {
	private readonly plugin: BrainPlugin;
	private debounceTimer: number | null = null;
	// Bumped on every new query so a slow, superseded request can't clobber a
	// newer one's rendered results if responses arrive out of order.
	private queryToken = 0;

	constructor(leaf: WorkspaceLeaf, plugin: BrainPlugin) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType(): string {
		return RELATED_VIEW_TYPE;
	}

	getDisplayText(): string {
		return "Related notes (Brain)";
	}

	getIcon(): string {
		return "brain";
	}

	async onOpen(): Promise<void> {
		this.body().addClass("brain-related-container");
		this.setStatus("Loading…", "brain-muted");
		this.scheduleQuery(0);
	}

	async onClose(): Promise<void> {
		if (this.debounceTimer !== null) {
			window.clearTimeout(this.debounceTimer);
			this.debounceTimer = null;
		}
	}

	/** Called by the plugin on active-leaf-change / file-open (already debounced by caller intent). */
	scheduleQuery(overrideMs?: number): void {
		if (this.debounceTimer !== null) window.clearTimeout(this.debounceTimer);
		const ms = overrideMs ?? this.plugin.settings.debounceMs;
		this.debounceTimer = window.setTimeout(() => {
			this.debounceTimer = null;
			void this.runQuery();
		}, Math.max(0, ms));
	}

	private body(): HTMLElement {
		return this.containerEl.children[1] as HTMLElement;
	}

	private setStatus(text: string, cls: string): void {
		const container = this.body();
		container.empty();
		container.createEl("p", { text, cls });
	}

	private async runQuery(): Promise<void> {
		const token = ++this.queryToken;

		if (!this.plugin.settings.autoRelated) {
			this.setStatus("Auto-related is off (see Obsidian Brain settings).", "brain-muted");
			return;
		}

		const file = this.app.workspace.getActiveFile();
		if (!file) {
			this.setStatus("No active note.", "brain-muted");
			return;
		}
		if (file.extension !== "md") {
			this.setStatus("Active file is not a markdown note.", "brain-muted");
			return;
		}

		let text = "";
		try {
			text = await this.app.vault.cachedRead(file);
		} catch {
			text = "";
		}
		// Title + first ~1500 chars, per the shared /ui/api/search contract.
		const query = `${file.basename}\n\n${text}`.slice(0, 1500);

		this.setStatus("Searching…", "brain-muted");
		try {
			const results = await this.plugin.brainClient.search(query, this.plugin.settings.topK);
			if (token !== this.queryToken) return; // superseded by a newer query
			const filtered = results.filter((r) => r.note_path !== file.path);
			this.renderResults(filtered);
		} catch (e) {
			if (token !== this.queryToken) return;
			this.setStatus(e instanceof Error ? e.message : String(e), "brain-error");
		}
	}

	private renderResults(results: SearchResult[]): void {
		const container = this.body();
		container.empty();
		if (!results.length) {
			container.createEl("p", { text: "No related notes found.", cls: "brain-muted" });
			return;
		}
		for (const r of results) {
			const item = container.createDiv({ cls: "brain-result" });
			const header = item.createDiv({ cls: "brain-result-header" });
			const link = header.createEl("a", { text: r.note_path, cls: "brain-result-link" });
			link.href = "#";
			link.addEventListener("click", (evt) => {
				evt.preventDefault();
				void this.openNote(r.note_path);
			});
			header.createEl("span", { text: r.score.toFixed(3), cls: "brain-score" });
			item.createEl("div", { text: (r.text || "").slice(0, 220), cls: "brain-snippet" });
		}
	}

	private async openNote(notePath: string): Promise<void> {
		const file = this.app.vault.getAbstractFileByPath(notePath);
		if (file instanceof TFile) {
			await this.app.workspace.getLeaf(false).openFile(file);
			return;
		}
		// Fallback for any path Obsidian's vault index doesn't resolve directly.
		await this.app.workspace.openLinkText(notePath, "", false);
	}
}
