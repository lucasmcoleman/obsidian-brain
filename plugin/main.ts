import { MarkdownView, Notice, Plugin, WorkspaceLeaf } from "obsidian";
import { BrainClient } from "./src/api";
import { AskBrainModal } from "./src/askModal";
import { RELATED_VIEW_TYPE, RelatedNotesView } from "./src/relatedView";
import { BrainPluginSettings, BrainSettingTab, DEFAULT_SETTINGS } from "./src/settings";

const HEALTH_POLL_INTERVAL_MS = 60_000;

export default class BrainPlugin extends Plugin {
	settings!: BrainPluginSettings;
	brainClient!: BrainClient;
	private statusBarItem: HTMLElement | null = null;

	async onload(): Promise<void> {
		await this.loadSettings();
		this.brainClient = new BrainClient(() => this.settings);

		this.registerView(RELATED_VIEW_TYPE, (leaf) => new RelatedNotesView(leaf, this));

		this.addRibbonIcon("brain", "Open related notes (Brain)", () => {
			void this.activateRelatedView();
		});

		this.addCommand({
			id: "open-related-notes-view",
			name: "Open related notes panel",
			callback: () => void this.activateRelatedView(),
		});

		this.addCommand({
			id: "ask-the-brain",
			name: "Ask the brain",
			callback: () => {
				// Capture whatever editor is active right now, before the modal opens
				// and steals focus, so we know whether to insert a link or open the note.
				const activeView = this.app.workspace.getActiveViewOfType(MarkdownView);
				const editor = activeView ? activeView.editor : null;
				new AskBrainModal(this.app, this, editor).open();
			},
		});

		this.addCommand({
			id: "refresh-brain-index",
			name: "Refresh brain index",
			callback: () => void this.refreshIndex(),
		});

		this.addSettingTab(new BrainSettingTab(this.app, this));

		// active-leaf-change covers switching panes/tabs; file-open covers opening
		// a note into the currently active pane. Both are debounced inside the view.
		this.registerEvent(
			this.app.workspace.on("active-leaf-change", () => this.refreshRelatedView()),
		);
		this.registerEvent(this.app.workspace.on("file-open", () => this.refreshRelatedView()));

		this.statusBarItem = this.addStatusBarItem();
		this.statusBarItem.addClass("brain-status");
		void this.updateHealthPill();
		this.registerInterval(
			window.setInterval(() => void this.updateHealthPill(), HEALTH_POLL_INTERVAL_MS),
		);
	}

	onunload(): void {
		this.app.workspace.detachLeavesOfType(RELATED_VIEW_TYPE);
	}

	async loadSettings(): Promise<void> {
		this.settings = Object.assign({}, DEFAULT_SETTINGS, ((await this.loadData()) as Partial<BrainPluginSettings>) ?? {});
	}

	async saveSettings(): Promise<void> {
		await this.saveData(this.settings);
	}

	async activateRelatedView(): Promise<void> {
		const { workspace } = this.app;
		const existing = workspace.getLeavesOfType(RELATED_VIEW_TYPE);
		let leaf: WorkspaceLeaf | null = existing.length > 0 ? existing[0] : null;
		if (!leaf) {
			leaf = workspace.getRightLeaf(false);
			if (leaf) await leaf.setViewState({ type: RELATED_VIEW_TYPE, active: true });
		}
		if (leaf) workspace.revealLeaf(leaf);
	}

	private refreshRelatedView(): void {
		for (const leaf of this.app.workspace.getLeavesOfType(RELATED_VIEW_TYPE)) {
			if (leaf.view instanceof RelatedNotesView) leaf.view.scheduleQuery();
		}
	}

	private async refreshIndex(): Promise<void> {
		new Notice("Refreshing brain index…");
		try {
			const result = await this.brainClient.refresh();
			new Notice(`Brain index: ${summarizeRefresh(result)}`);
		} catch (e) {
			new Notice(e instanceof Error ? e.message : "Brain index refresh failed.");
		}
	}

	private async updateHealthPill(): Promise<void> {
		if (!this.statusBarItem) return;
		const ok = await this.brainClient.health();
		this.statusBarItem.setText(ok ? "🧠 Brain: connected" : "🧠 Brain: unreachable");
		this.statusBarItem.toggleClass("brain-status-ok", ok);
		this.statusBarItem.toggleClass("brain-status-bad", !ok);
	}
}

/** Renders build_index's JSON result (status/notes/chunks, shape not guaranteed) into one line. */
function summarizeRefresh(result: unknown): string {
	if (result && typeof result === "object") {
		const r = result as Record<string, unknown>;
		const parts: string[] = [typeof r.status === "string" ? r.status : "done"];
		if (typeof r.notes === "number") parts.push(`${r.notes} notes`);
		if (typeof r.chunks === "number") parts.push(`${r.chunks} chunks`);
		return parts.join(" · ");
	}
	return "done";
}
