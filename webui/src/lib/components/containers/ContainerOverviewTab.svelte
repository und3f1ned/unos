<script lang="ts">
	// The Overview tab of a container's drawer: its facts as one list of labels over values (its
	// id with a copy button, creation, image, the app of the App Store that made it) and its
	// Docker labels behind a disclosure. The state is the drawer's header's alone (webui.ux-apps).
	import { Check, Copy, Package } from 'lucide-svelte';
	import { Button, TechDetails } from '$lib/components/ui';
	import type { Container } from '$lib/api/containers';
	import { intlLocale, t } from '$lib/i18n';
	import { formatDateTime } from '$lib/time';
	import { appOf } from './labels';

	type Props = {
		container: Container;
		/** The id was just copied: the button shows a check. */
		copied: boolean;
		oncopy: () => void;
	};

	let { container, copied, oncopy }: Props = $props();

	const labels = $derived(Object.entries(container.labels || {}));
	// As docker run --label takes them: one key=value a line, to copy.
	const labelText = $derived(labels.map(([key, value]) => `${key}=${value}`).join('\n'));
	const app = $derived(appOf(container));

	// It reads $intlLocale, so that an open drawer follows a change of the language; no seconds.
	function formatDate(dateStr: string): string {
		return formatDateTime(dateStr, $intlLocale);
	}
</script>

<div class="space-y-5">
	<div class="kv">
		<div class="col-span-full">
			<span class="kv-label">{$t('containers.drawer.id')}</span>
			<span class="kv-value flex items-start gap-2">
				<code class="mono-xs break-all select-all flex-1 min-w-0">{container.id}</code>
				<Button variant="ghost" size="icon-sm" class="-mt-1" onclick={oncopy} title={$t('containers.drawer.copyId')}>
					{#if copied}
						<Check class="w-4 h-4 text-success" aria-hidden="true" />
					{:else}
						<Copy class="w-4 h-4" aria-hidden="true" />
					{/if}
					<span class="sr-only">{$t('containers.drawer.copyId')}</span>
				</Button>
			</span>
		</div>
		<div>
			<span class="kv-label">{$t('containers.drawer.created')}</span>
			<span class="kv-value" data-field="created">{formatDate(container.created)}</span>
		</div>
		{#if app}
			<div>
				<span class="kv-label">{$t('containers.drawer.app')}</span>
				<span class="kv-value">
					<a href="/apps#installed" class="link inline-flex items-center gap-1">
						<Package class="w-3.5 h-3.5" aria-hidden="true" />
						{container.labels['unos.app.name'] ?? app}
					</a>
				</span>
			</div>
		{/if}
		<div class="col-span-full">
			<span class="kv-label">{$t('containers.drawer.image')}</span>
			<span class="kv-value"><code class="mono text-sm break-all">{container.image}</code></span>
		</div>
	</div>

	{#if app}
		<p class="text-xs text-content-secondary">{$t('containers.drawer.appHint')}</p>
	{/if}

	<!-- Docker's labels: technical, so behind a disclosure. -->
	{#if labels.length > 0}
		<TechDetails text={labelText} summary={$t('containers.drawer.labels', { n: labels.length })} />
	{/if}
</div>
