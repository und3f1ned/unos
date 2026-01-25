<script lang="ts">
	export let variant: 'success' | 'warning' | 'error' | 'info' | 'neutral' | 'purple' | 'cyan' | 'orange' = 'neutral';

	const variantClasses = {
		success: 'badge-success',
		warning: 'badge-warning',
		error: 'badge-error',
		info: 'badge-info',
		neutral: 'badge-neutral',
		purple: 'badge-purple',
		cyan: 'badge-cyan',
		orange: 'badge-orange'
	};

	$: classes = `badge ${variantClasses[variant]} ${$$props.class || ''}`;
</script>

<span class={classes}>
	<slot />
</span>
