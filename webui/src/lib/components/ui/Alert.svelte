<script lang="ts">
	import type { Snippet } from 'svelte';
	import { AlertCircle, CheckCircle, AlertTriangle, Info, X } from 'lucide-svelte';
	import { t } from '$lib/i18n';

	// An alert (ADR-0428): the 10 % tint of its colour with a bar on the left; the icon and the
	// title in the colour, the body in the text's colour, which reads better. `sm` for a note that
	// informs and does not warn.
	type Props = {
		variant?: 'error' | 'warning' | 'success' | 'info';
		size?: 'md' | 'sm';
		dismissible?: boolean;
		title?: string;
		class?: string;
		// The alert's close button was pressed (dismissible).
		ondismiss?: () => void;
		children?: Snippet;
	};

	let {
		variant = 'info',
		size = 'md',
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

<div class="alert {variantClasses[variant]} {size === 'sm' ? 'alert-sm' : ''} {className || ''}">
	<div class="flex items-start {size === 'sm' ? 'gap-2' : 'gap-3'}">
		<Icon
			class="alert-tone flex-shrink-0 {size === 'sm' ? 'w-4 h-4 mt-px' : 'w-[18px] h-[18px] mt-px'}"
			aria-hidden="true"
		/>
		<!-- A long word, such as a file's name, breaks: on a phone it would push the alert, and
		     its button, beyond the screen (ADR-0302). -->
		<div class="flex-1 min-w-0 break-words">
			{#if title}
				<p class="alert-title alert-tone">{title}</p>
			{/if}
			{@render children?.()}
		</div>
		{#if dismissible}
			<button
				type="button"
				class="flex-shrink-0 rounded text-content-secondary hover:text-content"
				onclick={() => ondismiss?.()}
				aria-label={$t('ui.alert.dismiss')}
			>
				<X class="w-4 h-4" />
			</button>
		{/if}
	</div>
</div>
