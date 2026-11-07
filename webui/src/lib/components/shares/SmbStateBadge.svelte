<script lang="ts">
	import type { SmbAccountState } from '$lib/api/shares';
	import { Badge } from '$lib/components/ui';

	/** The SMB state of a UNOS user. */
	export let state: SmbAccountState;
	/** Why the state is what it is, e.g. why SMB is not available: hover text, and read by screen readers. */
	export let hint = '';

	type Variant = 'success' | 'warning' | 'neutral';

	const badges: Record<SmbAccountState, { variant: Variant; label: string }> = {
		active: { variant: 'success', label: 'Active' },
		password_needed: { variant: 'warning', label: 'Password needed' },
		off: { variant: 'neutral', label: 'Off' },
		unavailable: { variant: 'neutral', label: 'Not available' }
	};

	$: badge = badges[state] ?? { variant: 'neutral', label: String(state) };
</script>

<!-- The hint is also in the text (sr-only): a title alone is out of reach of keyboard, touch and screen-reader users. -->
<span class="inline-flex" title={hint || undefined}>
	<Badge variant={badge.variant}>{badge.label}</Badge>{#if hint}<span class="sr-only"
			>{` (${hint})`}</span
		>{/if}
</span>
