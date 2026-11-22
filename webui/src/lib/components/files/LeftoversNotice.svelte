<script lang="ts">
	import { Trash2 } from 'lucide-svelte';
	import { Alert, Button } from '$lib/components/ui';
	import { formatSize } from '$lib/format';
	import { intlLocale, t } from '$lib/i18n';
	import type { UploadLeftovers } from './leftovers.svelte';

	type Props = {
		leftovers: UploadLeftovers;
		// Remove was chosen: the page asks in its dialog.
		onremove: () => void;
	};

	let { leftovers, onremove }: Props = $props();

	// The notice's Remove button, which takes the keyboard focus back when the dialog closes (state:
	// it comes and goes with the notice, and the compiler wants it reactive there).
	let leftoversButtonSlot: HTMLSpanElement | null = $state(null);

	export function focusRemove() {
		leftoversButtonSlot?.querySelector('button')?.focus();
	}
</script>

{#if leftovers.files.length > 0}
	<!-- A region named by its title, which screen readers can go to. -->
	<section aria-label={$t('files.leftovers.title')} class="mb-4">
		<Alert variant="warning" title={$t('files.leftovers.title')}>
			<p>
				{$t('files.leftovers.found', { n: leftovers.files.length, size: formatSize(leftovers.size, $intlLocale, $t) })}
			</p>
			<p class="mt-1">{$t('files.leftovers.hint')}</p>
			<div class="mt-2 flex flex-wrap gap-2">
				<span bind:this={leftoversButtonSlot} class="contents">
					<Button variant="secondary" size="sm" icon={Trash2} onclick={() => onremove()}>
						{$t('files.leftovers.remove')}
					</Button>
				</span>
			</div>
		</Alert>
	</section>
{/if}
