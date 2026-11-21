<script lang="ts">
	import { Button, Input } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { FileBrowser } from './browser.svelte';

	type Props = {
		browser: FileBrowser;
	};

	let { browser }: Props = $props();
</script>

<!-- New Folder: on a narrow screen the buttons wrap below the field (ADR-0302). -->
{#if browser.showNewFolder}
	<div class="mb-4 flex flex-wrap items-center gap-2">
		<Input
			bind:value={browser.newFolderName}
			placeholder={$t('files.folderName')}
			class="flex-1 min-w-[10rem]"
			onkeydown={(e) => e.key === 'Enter' && browser.createFolder()}
		/>
		<div class="flex gap-2">
			<Button onclick={() => browser.createFolder()}>{$t('files.create')}</Button>
			<Button variant="secondary" onclick={() => browser.cancelNewFolder()}>
				{$t('common.cancel')}
			</Button>
		</div>
	</div>
{/if}
