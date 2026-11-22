<script lang="ts">
	import { tick } from 'svelte';
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import type { FileBrowser } from './browser.svelte';
	import type { UploadLeftovers } from './leftovers.svelte';

	type Props = {
		browser: FileBrowser;
		leftovers: UploadLeftovers;
		// The dialog closed without removing: the page gives the keyboard focus back to the notice.
		onclosed: () => void;
	};

	let { browser, leftovers, onclosed }: Props = $props();

	// The dialog's Cancel takes the keyboard focus: a removal cannot be undone (ADR-0354).
	export function ask() {
		leftovers.ask();
	}

	async function closeLeftovers() {
		if (leftovers.sending) return;
		leftovers.removing = null;
		await tick();
		onclosed();
	}

	// A refusal that leaves none of the dialog's files listed closes it: the reason stays above the
	// list.
	async function handleRemoveLeftovers() {
		if (!leftovers.removing || leftovers.sending) return;
		browser.error = '';
		const outcome = await leftovers.remove();
		if (outcome === 'signed-out') return;
		if (outcome === 'unlisted') browser.error = `${$t('files.leftovers.failed')}: ${leftovers.error}`;
		// The open folder may have listed them.
		await browser.refresh();
	}
</script>

<ConfirmDialog
	open={leftovers.removing !== null}
	title={$t('files.leftovers.dialogTitle', { n: leftovers.removing?.length ?? 0 })}
	message={leftovers.removing ? $t('files.leftovers.confirm', { n: leftovers.removing.length }) : ''}
	confirmLabel={$t('files.leftovers.removeForGood')}
	busy={leftovers.sending}
	focusCancel
	oncancel={closeLeftovers}
	onconfirm={handleRemoveLeftovers}
>
	{#if leftovers.removing}
		<ul class="space-y-2">
			{#each leftovers.removing as file (file.path)}
				<li class="card-nested px-3 py-2 text-sm">
					<p class="font-mono break-all">{file.path}</p>
					<p class="text-content-secondary">
						{$t('files.leftovers.file', {
							size: formatSize(file.size, $intlLocale, $t),
							time: formatDateTime(file.modified, $intlLocale)
						})}
					</p>
				</li>
			{/each}
		</ul>
		{#if leftovers.error}
			<!-- role="alert": screen readers announce the server's refusal. -->
			<div role="alert">
				<Alert variant="error" title={$t('files.leftovers.failed')}>{leftovers.error}</Alert>
			</div>
		{/if}
	{/if}
</ConfirmDialog>
