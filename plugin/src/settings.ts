import { App, PluginSettingTab, Setting } from "obsidian";
import type BrainPlugin from "../main";

export interface BrainPluginSettings {
	/** Base URL of the obsidian-brain HTTP server, e.g. http://localhost:8053 */
	baseUrl: string;
	/** Bearer token matching the server's BRAIN_AUTH_TOKEN (blank if the server has none). */
	token: string;
	/** How many distinct notes to request per search/related query. */
	topK: number;
	/** Whether the related-notes panel re-queries automatically on note switch. */
	autoRelated: boolean;
	/** Debounce delay (ms) before the related-notes panel re-queries after a note switch. */
	debounceMs: number;
}

export const DEFAULT_SETTINGS: BrainPluginSettings = {
	baseUrl: "http://localhost:8053",
	token: "",
	topK: 5,
	autoRelated: true,
	debounceMs: 400,
};

export class BrainSettingTab extends PluginSettingTab {
	private plugin: BrainPlugin;

	constructor(app: App, plugin: BrainPlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl("h2", { text: "Obsidian Brain" });
		containerEl.createEl("p", {
			cls: "setting-item-description",
			text:
				"Connects to a running obsidian-brain server (see the plugin's README for " +
				"deployment / SSH-forward notes if the server isn't on this machine).",
		});

		new Setting(containerEl)
			.setName("Server base URL")
			.setDesc("e.g. http://localhost:8053 (no trailing slash needed).")
			.addText((text) =>
				text
					.setPlaceholder(DEFAULT_SETTINGS.baseUrl)
					.setValue(this.plugin.settings.baseUrl)
					.onChange(async (value) => {
						const trimmed = value.trim();
						this.plugin.settings.baseUrl = trimmed || DEFAULT_SETTINGS.baseUrl;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Bearer token")
			.setDesc(
				"Matches BRAIN_AUTH_TOKEN on the server. Stored in plaintext in this " +
					"plugin's data.json — use a dedicated token, not a shared vault secret.",
			)
			.addText((text) => {
				text.inputEl.type = "password";
				text
					.setPlaceholder("leave blank if the server has no token")
					.setValue(this.plugin.settings.token)
					.onChange(async (value) => {
						this.plugin.settings.token = value.trim();
						await this.plugin.saveSettings();
					});
			});

		new Setting(containerEl)
			.setName("Results per query (top_k)")
			.setDesc("How many distinct notes to return per search / related-notes query.")
			.addText((text) =>
				text
					.setPlaceholder(String(DEFAULT_SETTINGS.topK))
					.setValue(String(this.plugin.settings.topK))
					.onChange(async (value) => {
						const n = parseInt(value, 10);
						this.plugin.settings.topK =
							Number.isFinite(n) && n > 0 ? n : DEFAULT_SETTINGS.topK;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName("Auto-update related notes")
			.setDesc("Re-query the related-notes panel automatically when you switch notes.")
			.addToggle((toggle) =>
				toggle.setValue(this.plugin.settings.autoRelated).onChange(async (value) => {
					this.plugin.settings.autoRelated = value;
					await this.plugin.saveSettings();
				}),
			);

		new Setting(containerEl)
			.setName("Debounce (ms)")
			.setDesc("Delay after switching notes before the related-notes panel re-queries.")
			.addText((text) =>
				text
					.setPlaceholder(String(DEFAULT_SETTINGS.debounceMs))
					.setValue(String(this.plugin.settings.debounceMs))
					.onChange(async (value) => {
						const n = parseInt(value, 10);
						this.plugin.settings.debounceMs =
							Number.isFinite(n) && n >= 0 ? n : DEFAULT_SETTINGS.debounceMs;
						await this.plugin.saveSettings();
					}),
			);
	}
}
