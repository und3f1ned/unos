<script lang="ts">
	// The dot after an installed app's name in the catalog: green while it runs.
	type Props = {
		running: boolean;
	};

	let { running }: Props = $props();
</script>

<div
	class="w-2 h-2 rounded-full flex-shrink-0 {running
		? 'bg-status-success'
		: 'bg-status-neutral'}"
></div>
