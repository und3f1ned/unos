<script lang="ts">
	import { t } from '$lib/i18n';

	// The dot after an installed app's name in the catalog: green while it runs. An image with its
	// state as its name, so that the state is not carried by the colour alone (ADR-0428).
	type Props = {
		running: boolean;
	};

	let { running }: Props = $props();
</script>

<div
	role="img"
	aria-label={running ? $t('apps.state.running') : $t('apps.state.stopped')}
	class="w-2 h-2 rounded-full flex-shrink-0 {running
		? 'bg-status-success'
		: 'bg-status-neutral'}"
></div>
