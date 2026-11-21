<script lang="ts">
	import { beforeNavigate } from '$app/navigation';
	import ConfirmDialog from './ConfirmDialog.svelte';
	import { cancelAll, confirmQueue } from './confirm';

	// The dialog of confirm() (ADR-0432), once in the root layout: it shows the first question of
	// the queue and answers it.
	const request = $derived($confirmQueue[0] ?? null);
	// A question of a page that goes is answered no: its action never runs on another page.
	beforeNavigate(() => cancelAll());
</script>

{#if request}
	{#key request.id}
		<ConfirmDialog
			open
			title={request.title}
			message={request.message}
			consequences={request.consequences}
			warning={request.warning}
			confirmLabel={request.confirmLabel}
			cancelLabel={request.cancelLabel}
			tone={request.tone}
			typeToConfirm={request.typeToConfirm}
			focusCancel={request.focusCancel}
			onconfirm={() => request.answer(true)}
			oncancel={() => request.answer(false)}
		/>
	{/key}
{/if}
