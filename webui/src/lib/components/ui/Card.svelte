<script lang="ts">
	export let nested: boolean = false;
	export let padding: 'none' | 'sm' | 'md' | 'lg' = 'md';

	const paddingClasses = {
		none: 'p-0',
		sm: 'p-3',
		md: 'p-4',
		lg: 'p-6'
	};

	$: baseClass = nested ? 'card-nested' : 'card';
	$: classes = `${baseClass} ${paddingClasses[padding]} ${$$props.class || ''}`.replace('p-4', paddingClasses[padding]);
</script>

<div class={classes}>
	<slot />
</div>

<style>
	/* Override default padding from .card if custom padding is specified */
	:global(.card-nested) {
		padding: inherit;
	}
</style>
