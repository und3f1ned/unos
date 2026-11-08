<script lang="ts">
	import type { SmbAccountState } from '$lib/api/shares';
	import { Badge } from '$lib/components/ui';
	import { t } from '$lib/i18n';

	/** The SMB state of a UNOS user. */
	export let state: SmbAccountState;
	/** Why the state is what it is, e.g. why SMB is not available: hover text, and read by screen readers. */
	export let hint = '';

	type Variant = 'success' | 'warning' | 'neutral';

	// The server's states, each with the key of its label; a state without one is shown as it comes.
	const badges: Record<SmbAccountState, { variant: Variant; key: string }> = {
		active: { variant: 'success', key: 'shares.smbState.active' },
		password_needed: { variant: 'warning', key: 'shares.smbState.passwordNeeded' },
		off: { variant: 'neutral', key: 'shares.smbState.off' },
		unavailable: { variant: 'neutral', key: 'shares.smbState.unavailable' }
	};

	$: badge = badges[state] as { variant: Variant; key: string } | undefined;
</script>

<!-- The hint is also in the text (sr-only): a title alone is out of reach of keyboard, touch and screen-reader users.
     Relative, so that the hint, placed absolutely, stays in the badge: in a list that scrolls on its own, such as
     that of a share's dialog, it would make the dialog's overlay scroll on below the dialog (ADR-0301). -->
<span class="relative inline-flex" title={hint || undefined}>
	<Badge variant={badge?.variant ?? 'neutral'}>{badge ? $t(badge.key) : String(state)}</Badge>{#if hint}<span
			class="sr-only">{` (${hint})`}</span
		>{/if}
</span>
