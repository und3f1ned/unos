<script lang="ts">
	import { AlertCircle, AlertTriangle, CheckCircle, Info, X } from 'lucide-svelte';
	import { beforeNavigate } from '$app/navigation';
	import { t } from '$lib/i18n';
	import { dismissToast, dropActions, toasts, type Toast, type ToastTone } from './toast';

	// The toasts of the page (ADR-0432), at the bottom right, at the bottom on a phone. Two live
	// regions, drawn from the start so that screen readers hear what comes into them: a polite one
	// for success and information, an assertive one for errors and warnings. No role="status" or
	// "alert": the specs read a page's own status and alerts by those roles. A toast goes after its
	// time, which stands still while the pointer or the focus is on it.
	const ICONS = { success: CheckCircle, error: AlertCircle, warning: AlertTriangle, info: Info };
	const urgent = (tone: ToastTone) => tone === 'error' || tone === 'warning';

	// A toast's action belongs to its page: it goes with it.
	beforeNavigate(() => dropActions());

	// Where the keyboard focus was before it came into a toast: it goes back there when the toast
	// that holds it closes, not to the start of the page.
	let region: HTMLDivElement | null = $state(null);
	let returnTo: HTMLElement | null = null;

	function focusIn(event: FocusEvent) {
		const from = event.relatedTarget;
		if (from instanceof HTMLElement && !region?.contains(from)) returnTo = from;
	}

	// The timers, by toast: the time left and since when it runs.
	const timers = new Map<number, { left: number; since: number; handle: ReturnType<typeof setTimeout> | null }>();

	function start(toast: Toast) {
		const timer = timers.get(toast.id) ?? { left: toast.duration, since: 0, handle: null };
		if (timer.handle !== null) return;
		timer.since = Date.now();
		timer.handle = setTimeout(() => close(toast.id), timer.left);
		timers.set(toast.id, timer);
	}

	function pause(toast: Toast) {
		const timer = timers.get(toast.id);
		if (!timer || timer.handle === null) return;
		clearTimeout(timer.handle);
		timer.handle = null;
		timer.left = Math.max(1000, timer.left - (Date.now() - timer.since));
	}

	function close(id: number) {
		const timer = timers.get(id);
		if (timer?.handle) clearTimeout(timer.handle);
		timers.delete(id);
		const focused = document.activeElement;
		const hadFocus = !!focused && !!region?.querySelector(`[data-toast-id="${id}"]`)?.contains(focused);
		dismissToast(id);
		if (hadFocus && returnTo?.isConnected) returnTo.focus();
	}

	// The pointer left: the time runs again, unless the keyboard focus is still in the toast.
	function leave(event: PointerEvent, toast: Toast) {
		const node = event.currentTarget as HTMLElement;
		if (!node.contains(document.activeElement)) start(toast);
	}

	// Each toast starts its time when it is drawn, and stops it when it goes.
	function timed(node: HTMLElement, toast: Toast) {
		start(toast);
		return {
			destroy() {
				const timer = timers.get(toast.id);
				if (timer?.handle) clearTimeout(timer.handle);
				timers.delete(toast.id);
			}
		};
	}
</script>

{#snippet item(toast: Toast)}
	{@const Icon = ICONS[toast.tone]}
	<!-- The pointer on it only holds its time: no interaction of its own. -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="toast toast-{toast.tone}"
		data-toast={toast.tone}
		data-toast-id={toast.id}
		use:timed={toast}
		onpointerenter={() => pause(toast)}
		onpointerleave={(event) => leave(event, toast)}
		onfocusin={() => pause(toast)}
		onfocusout={() => start(toast)}
	>
		<Icon class="toast-icon" aria-hidden="true" />
		<div class="min-w-0 flex-1">
			{#if toast.title}<p class="font-semibold">{toast.title}</p>{/if}
			<p class="break-words">{toast.message}</p>
		</div>
		{#if toast.action}
			<button
				type="button"
				class="btn btn-ghost btn-sm shrink-0 text-content-link"
				onclick={() => {
					toast.action?.run();
					close(toast.id);
				}}>{toast.action.label}</button
			>
		{/if}
		<button
			type="button"
			class="btn btn-ghost btn-icon-sm shrink-0 -my-1 -mr-1"
			aria-label={$t('ui.toast.dismiss')}
			onclick={() => close(toast.id)}
		>
			<X class="w-4 h-4" aria-hidden="true" />
		</button>
	</div>
{/snippet}

<div class="toast-region" bind:this={region} onfocusin={focusIn}>
	<div aria-live="polite" aria-relevant="additions" class="toast-stack">
		{#each $toasts.filter((toast) => !urgent(toast.tone)) as toast (toast.id)}
			{@render item(toast)}
		{/each}
	</div>
	<div aria-live="assertive" aria-relevant="additions" class="toast-stack">
		{#each $toasts.filter((toast) => urgent(toast.tone)) as toast (toast.id)}
			{@render item(toast)}
		{/each}
	</div>
</div>
