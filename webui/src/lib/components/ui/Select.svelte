<script lang="ts">
	export let id: string = '';
	export let value: string = '';
	export let label: string = '';
	export let hint: string = '';
	export let error: string = '';
	export let required: boolean = false;
	export let disabled: boolean = false;
	export let options: Array<{ value: string; label: string }> = [];

	$: inputId = id || `select-${Math.random().toString(36).slice(2, 9)}`;
</script>

<div class={$$props.class || ''}>
	{#if label}
		<label for={inputId} class="form-label">
			{label}
			{#if required}<span class="text-error">*</span>{/if}
		</label>
	{/if}

	<select
		id={inputId}
		bind:value
		{required}
		{disabled}
		class="form-select"
		class:border-red-500={error}
		on:change
	>
		<slot>
			{#each options as option}
				<option value={option.value}>{option.label}</option>
			{/each}
		</slot>
	</select>

	{#if error}
		<p class="form-error">{error}</p>
	{:else if hint}
		<p class="form-hint">{hint}</p>
	{/if}
</div>
