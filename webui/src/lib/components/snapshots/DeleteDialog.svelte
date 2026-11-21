<script lang="ts" generics="Item extends { name: string; path: string }">
	// The dialog that deletes a snapshot or a copy that a restore made, with the texts of each.
	import { Alert, ConfirmDialog } from '$lib/components/ui';
	import { messageOf } from './names';

	type Props = {
		/** What to delete; the dialog is open while it is set. */
		item: Item | null;
		title: string;
		/** The sentence around the name and the path: its part before them and its part after. */
		before: string;
		after: string;
		/**
		 * A note under the sentence, if any. Without one, the markup puts nothing between the
		 * sentence and the error, not even a space, as the snapshot's dialog had it.
		 */
		note?: string;
		/** The title of the error. */
		failed: string;
		/** The delete button's text. */
		submit: string;
		/** Asks the server to delete the item. */
		remove: (item: Item) => Promise<unknown>;
		onclose: () => void;
		/** The item was deleted. */
		ondeleted: () => void | Promise<void>;
	};

	let { item, title, before, after, note = '', failed, submit, remove, onclose, ondeleted }: Props = $props();

	let busy = $state(false);
	let error = $state('');

	function close() {
		if (busy) return;
		error = '';
		onclose();
	}

	async function confirm() {
		if (!item || busy) return;
		busy = true;
		error = '';
		try {
			await remove(item);
			busy = false;
			await ondeleted();
		} catch (e) {
			// For a copy: 409 when in use, busy or holding subvolumes; 503 when Docker did not answer
			error = messageOf(e);
		} finally {
			busy = false;
		}
	}
</script>

<ConfirmDialog open={item !== null} {title} confirmLabel={submit} {busy} oncancel={close} onconfirm={confirm}>
	<!-- The note follows the paragraph with no line break, which would be a second space in the
	     dialog's text: Prettier leaves the block as it is written. -->
	<!-- prettier-ignore -->
	{#if item}
		<p class="text-sm">
			{before}
			<span class="font-semibold break-all">{item.name}</span>
			(<span class="mono-xs break-all">{item.path}</span>){after}
		</p>{#if note}{' '}<p class="text-sm text-secondary">{note}</p>{/if}
		{#if error}
			<Alert variant="error" title={failed}>{error}</Alert>
		{/if}
	{/if}
</ConfirmDialog>
