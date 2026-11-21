<script lang="ts">
	// The folders or variables of one of an app's containers in its details: an app of several
	// containers groups them per container, under the container's name (ADR-0381).
	import type { Snippet } from 'svelte';

	type Props = {
		/** The container's name. */
		name: string;
		/** What the group holds, the start of its heading's id. */
		kind: 'volumes' | 'environment';
		/** The app has several containers: the group is named; one alone is not. */
		several: boolean;
		children: Snippet;
	};

	let { name, kind, several, children }: Props = $props();

	const headingId = $derived(`app-${kind}-${name}`);
</script>

<div class="space-y-3" role={several ? 'group' : undefined} aria-labelledby={several ? headingId : undefined}>
	{#if several}
		<h4 id={headingId} class="text-sm font-medium mono break-all pt-2">
			{name}
		</h4>
	{/if}
	{@render children()}
</div>
