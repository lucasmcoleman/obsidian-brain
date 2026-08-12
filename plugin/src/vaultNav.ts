import { App, TFile } from "obsidian";

/** Open a note by vault path, falling back to Obsidian's link resolution if it isn't found directly. */
export async function openVaultNote(app: App, notePath: string): Promise<void> {
	const file = app.vault.getAbstractFileByPath(notePath);
	if (file instanceof TFile) {
		await app.workspace.getLeaf(false).openFile(file);
		return;
	}
	// Fallback for any path Obsidian's vault index doesn't resolve directly.
	await app.workspace.openLinkText(notePath, "", false);
}
