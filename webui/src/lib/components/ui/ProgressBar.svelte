<script lang="ts">
	type Variant = 'success' | 'warning' | 'error' | 'info';

	type Props = {
		value?: number;
		max?: number;
		size?: 'sm' | 'md';
		variant?: Variant | 'auto';
		showLabel?: boolean;
		/** What the bar measures, for screen readers (ADR-0197). */
		label?: string | undefined;
		/** The value as words, where a percent of max would mislead (a temperature). */
		valueText?: string | undefined;
		class?: string;
	};

	let {
		value = 0,
		max = 100,
		size = 'md',
		variant = 'auto',
		showLabel = false,
		label = undefined,
		valueText = undefined,
		class: className = ''
	}: Props = $props();

	const percent = $derived(max > 0 ? Math.min(100, (value / max) * 100) : 0);
	const autoVariant: Variant = $derived(percent < 70 ? 'success' : percent < 90 ? 'warning' : 'error');
	const actualVariant: Variant = $derived(variant === 'auto' ? autoVariant : variant);

	const fillClasses: Record<Variant, string> = {
		success: 'progress-fill-success',
		warning: 'progress-fill-warning',
		error: 'progress-fill-error',
		info: 'progress-fill-info'
	};

	const sizeClass = $derived(size === 'sm' ? 'progress-bar-sm' : '');
</script>

<div class={className || ''}>
	<div class="progress-bar {sizeClass}">
		<div
			class="progress-bar-fill {fillClasses[actualVariant]}"
			style="width: {percent}%"
			role="progressbar"
			aria-label={label}
			aria-valuetext={valueText}
			aria-valuenow={value}
			aria-valuemin="0"
			aria-valuemax={max}
		></div>
	</div>
	{#if showLabel}
		<span class="block mt-1 text-right text-xs text-secondary tabular">{percent.toFixed(1)}%</span>
	{/if}
</div>
