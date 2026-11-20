<script lang="ts">
	import type { Snippet } from 'svelte';
	import Modal from './Modal.svelte';

	// A side panel (ADR-0432): a dialog at the right edge as tall as the window, the whole screen of
	// a phone, for what needs room: a form of more than six fields, a list, an object's details. It
	// is a Modal (the stack, Escape, the focus, inert), placed at the side; its header and footer
	// stay while its body scrolls.
	type Props = {
		open?: boolean;
		title: string;
		description?: string;
		/** md 560 px, lg 720 px. */
		size?: 'md' | 'lg';
		closable?: boolean;
		onclose?: () => void;
		titleIcon?: Snippet;
		status?: Snippet;
		footer?: Snippet;
		children?: Snippet;
	};

	let {
		open = false,
		title,
		description = '',
		size = 'md',
		closable = true,
		onclose,
		titleIcon,
		status,
		footer,
		children
	}: Props = $props();
</script>

<Modal {open} {title} {description} {size} {closable} {onclose} {titleIcon} {status} {footer} placement="side">
	{@render children?.()}
</Modal>
