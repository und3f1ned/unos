<script lang="ts">
	import { ClipboardPaste } from 'lucide-svelte';
	import { Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { FileBrowser } from './browser.svelte';

	type Props = {
		browser: FileBrowser;
	};

	let { browser }: Props = $props();
</script>

<!-- Copy or move in progress: the destination is the folder that is open -->
{#if browser.pending}
	<div class="mb-4 flex flex-wrap items-center gap-2 card-nested py-2">
		<span class="text-sm text-content-secondary mr-2">
			{$t(browser.pending.mode === 'copy' ? 'files.pending.copy' : 'files.pending.move', {
				n: browser.pending.paths.length
			})}
		</span>
		<Button
			variant="primary"
			size="sm"
			icon={ClipboardPaste}
			loading={browser.busy}
			disabled={browser.renameSent}
			onclick={() => browser.finishTransfer()}
		>
			{browser.pending.mode === 'copy' ? $t('files.copyHere') : $t('files.moveHere')}
		</Button>
		<Button variant="ghost" size="sm" disabled={browser.working} onclick={() => (browser.pending = null)}
			>{$t('common.cancel')}</Button
		>
	</div>
{/if}
