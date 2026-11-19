<script lang="ts">
	type Props = {
		status?: 'success' | 'warning' | 'error' | 'neutral';
		pulse?: boolean;
		class?: string;
	};

	let { status = 'neutral', pulse = false, class: className = '' }: Props = $props();

	const statusClasses = {
		success: 'status-dot-success',
		warning: 'status-dot-warning',
		error: 'status-dot-error',
		neutral: 'status-dot-neutral'
	};
</script>

<span
	class="status-dot {statusClasses[status]} {pulse ? 'animate-pulse' : ''} {className || ''}"
></span>
