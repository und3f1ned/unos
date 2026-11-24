<script lang="ts">
	// A planned repair of a pool (ADR-0500): its unmount or removal was refused because containers use it,
	// and critical apps of the SD standby are among them. The offer moves those apps to the card first, as
	// their pool's failure would, and the dialog then tries again; another container that still uses the
	// pool is named by the server's answer.
	import { Alert, Button } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	type Props = {
		/** The critical apps of the pool that still run from it. */
		apps: string[];
		busy: boolean;
		onmove: () => void;
	};

	let { apps, busy, onmove }: Props = $props();
</script>

<Alert variant="warning" title={$t('storage.criticalApps.title')}>
	<p data-testid="critical-apps-offer">{$t('storage.criticalApps.text', { apps: apps.join(', ') })}</p>
	<Button variant="secondary" size="sm" class="mt-2" loading={busy} onclick={onmove}>
		{$t('storage.criticalApps.move')}
	</Button>
</Alert>
