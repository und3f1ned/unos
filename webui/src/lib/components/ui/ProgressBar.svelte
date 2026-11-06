<script lang="ts">
	type Variant = 'success' | 'warning' | 'error' | 'info';

	export let value: number = 0;
	export let max: number = 100;
	export let size: 'sm' | 'md' = 'md';
	export let variant: Variant | 'auto' = 'auto';
	export let showLabel: boolean = false;

	$: percent = max > 0 ? Math.min(100, (value / max) * 100) : 0;

	let autoVariant: Variant;
	$: autoVariant = percent < 70 ? 'success' : percent < 90 ? 'warning' : 'error';
	let actualVariant: Variant;
	$: actualVariant = variant === 'auto' ? autoVariant : variant;

	const fillClasses: Record<Variant, string> = {
		success: 'progress-fill-success',
		warning: 'progress-fill-warning',
		error: 'progress-fill-error',
		info: 'progress-fill-info'
	};

	$: sizeClass = size === 'sm' ? 'progress-bar-sm' : '';
</script>

<div class={$$props.class || ''}>
	<div class="progress-bar {sizeClass}">
		<div
			class="progress-bar-fill {fillClasses[actualVariant]}"
			style="width: {percent}%"
			role="progressbar"
			aria-valuenow={value}
			aria-valuemin="0"
			aria-valuemax={max}
		/>
	</div>
	{#if showLabel}
		<span class="text-xs text-secondary mt-1">{percent.toFixed(1)}%</span>
	{/if}
</div>
