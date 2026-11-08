<script lang="ts">
	import { AlertCircle, CheckCircle, AlertTriangle, Info, X } from 'lucide-svelte';
	import { createEventDispatcher } from 'svelte';
	import { t } from '$lib/i18n';

	export let variant: 'error' | 'warning' | 'success' | 'info' = 'info';
	export let dismissible: boolean = false;
	export let title: string = '';

	const dispatch = createEventDispatcher();

	const variantClasses = {
		error: 'alert-error',
		warning: 'alert-warning',
		success: 'alert-success',
		info: 'alert-info'
	};

	const icons = {
		error: AlertCircle,
		warning: AlertTriangle,
		success: CheckCircle,
		info: Info
	};

	$: Icon = icons[variant];
</script>

<div class="alert {variantClasses[variant]} {$$props.class || ''}">
	<div class="flex items-start gap-3">
		<Icon class="w-5 h-5 flex-shrink-0 mt-0.5" />
		<!-- A long word, such as a file's name, breaks: on a phone it would push the alert, and
		     its button, beyond the screen (ADR-0302). -->
		<div class="flex-1 min-w-0 break-words">
			{#if title}
				<p class="font-medium mb-1">{title}</p>
			{/if}
			<slot />
		</div>
		{#if dismissible}
			<button
				type="button"
				class="flex-shrink-0 hover:opacity-70 transition-opacity"
				on:click={() => dispatch('dismiss')}
				aria-label={$t('ui.alert.dismiss')}
			>
				<X class="w-4 h-4" />
			</button>
		{/if}
	</div>
</div>
