<script lang="ts">
	export let status: 'success' | 'warning' | 'error' | 'neutral' = 'neutral';
	export let pulse: boolean = false;

	const statusClasses = {
		success: 'status-dot-success',
		warning: 'status-dot-warning',
		error: 'status-dot-error',
		neutral: 'status-dot-neutral'
	};
</script>

<span
	class="status-dot {statusClasses[status]} {pulse ? 'animate-pulse' : ''} {$$props.class || ''}"
/>
