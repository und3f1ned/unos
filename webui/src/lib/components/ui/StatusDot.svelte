<script lang="ts">
	type Props = {
		status?: 'success' | 'warning' | 'error' | 'info' | 'neutral';
		/** A job that runs (a scrub, a balance): the dot pulses. */
		pulse?: boolean;
		/** The state the dot shows, for screen readers: a dot is never the only carrier of a state. */
		label?: string;
		class?: string;
	};

	let { status = 'neutral', pulse = false, label = '', class: className = '' }: Props = $props();

	const statusClasses = {
		success: 'status-dot-success',
		warning: 'status-dot-warning',
		error: 'status-dot-error',
		info: 'status-dot-info',
		neutral: 'status-dot-neutral'
	};
</script>

<!-- The pulse stops under reduced motion (app.css). -->
<span
	class="status-dot {statusClasses[status]} {pulse ? 'animate-pulse' : ''} {className || ''}"
	aria-hidden={label ? undefined : 'true'}
></span>{#if label}<span class="sr-only">{label}</span>{/if}
