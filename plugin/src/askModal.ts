import { App, Editor, Modal } from "obsidian";
import type BrainPlugin from "../main";
import type { SearchResult } from "./api";
import { openVaultNote } from "./vaultNav";

/**
 * "Ask the brain" command modal: queries GET /ui/api/search and, on click,
 * either inserts a [[wikilink]] at the captured editor's cursor or opens the
 * note directly if no editor was active when the command was invoked.
 */
export class AskBrainModal extends Modal {
	private readonly plugin: BrainPlugin;
	private readonly targetEditor: Editor | null;
	private inputEl!: HTMLInputElement;
	private resultsEl!: HTMLElement;

	constructor(app: App, plugin: BrainPlugin, targetEditor: Editor | null) {
		super(app);
		this.plugin = plugin;
		this.targetEditor = targetEditor;
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.empty();
		contentEl.addClass("brain-ask-modal");
		contentEl.createEl("h2", { text: "Ask the brain" });

		const row = contentEl.createDiv({ cls: "brain-ask-row" });
		this.inputEl = row.createEl("input", {
			type: "text",
			attr: { placeholder: "Ask about people, projects, decisions…" },
		});
		const button = row.createEl("button", { text: "Search", cls: "mod-cta" });

		this.resultsEl = contentEl.createDiv({ cls: "brain-ask-results" });

		const run = () => void this.runQuery();
		button.addEventListener("click", run);
		this.inputEl.addEventListener("keydown", (evt) => {
			if (evt.key === "Enter") run();
		});
		this.inputEl.focus();
	}

	onClose(): void {
		this.contentEl.empty();
	}

	private async runQuery(): Promise<void> {
		const q = this.inputEl.value.trim();
		if (!q) return;
		this.resultsEl.empty();
		this.resultsEl.createEl("p", { text: "Searching…", cls: "brain-muted" });
		try {
			const results = await this.plugin.brainClient.search(q, this.plugin.settings.topK);
			this.renderResults(results);
		} catch (e) {
			this.resultsEl.empty();
			this.resultsEl.createEl("p", {
				text: e instanceof Error ? e.message : String(e),
				cls: "brain-error",
			});
		}
	}

	private renderResults(results: SearchResult[]): void {
		this.resultsEl.empty();
		if (!results.length) {
			this.resultsEl.createEl("p", { text: "No results.", cls: "brain-muted" });
			return;
		}
		for (const r of results) {
			const item = this.resultsEl.createDiv({ cls: "brain-result" });
			const header = item.createDiv({ cls: "brain-result-header" });
			const link = header.createEl("a", { text: r.note_path, cls: "brain-result-link" });
			link.href = "#";
			header.createEl("span", { text: r.score.toFixed(3), cls: "brain-score" });
			item.createEl("div", { text: (r.text || "").slice(0, 240), cls: "brain-snippet" });
			link.addEventListener("click", (evt) => {
				evt.preventDefault();
				this.chooseResult(r.note_path);
			});
		}
	}

	private chooseResult(notePath: string): void {
		const basename = (notePath.split("/").pop() ?? notePath).replace(/\.md$/i, "");
		if (this.targetEditor) {
			this.targetEditor.replaceSelection(`[[${basename}]]`);
			this.close();
			return;
		}
		this.close();
		void openVaultNote(this.app, notePath);
	}
}
