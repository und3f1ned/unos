<script lang="ts">
	import type { Snippet } from 'svelte';
	import { AlertCircle, CheckCircle, AlertTriangle, Info, X } from 'lucide-svelte';
	import { t } from '$lib/i18n';

	type Props = {
		variant?: 'error' | 'warning' | 'success' | 'info';
		dismissible?: boolean;
		title?: string;
		class?: string;
		// The alert's close button was pressed (dismissible).
		ondismiss?: () => void;
		children?: Snippet;
	};

	let {
		variant = 'info',
		dismissible = false,
		title = '',
		class: className = '',
		ondismiss,
		children
	}: Props = $props();

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

	const Icon = $derived(icons[variant]);
</script>

<div class="alert {variantClasses[variant]} {className || ''}">
	<div class="flex items-start gap-3">
		<Icon class="w-5 h-5 flex-shrink-0 mt-0.5" />
		<!-- A long word, such as a file's name, breaks: on a phone it would push the alert, and
		     its button, beyond the screen (ADR-0302). -->
		<div class="flex-1 min-w-0 break-words">
			{#if title}
				<p class="font-medium mb-1">{title}</p>
			{/if}
			{@render children?.()}
		</div>
		{#if dismissible}
			<button
				type="button"
				class="flex-shrink-0 hover:opacity-70 transition-opacity"
				onclick={() => ondismiss?.()}
				aria-label={$t('ui.alert.dismiss')}
			>
				<X class="w-4 h-4" />
			</button>
		{/if}
	</div>
</div>
