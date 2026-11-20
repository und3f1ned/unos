<script lang="ts">
	import { MoreHorizontal } from 'lucide-svelte';
	import { t } from '$lib/i18n';
	import Menu, { type MenuItem } from './Menu.svelte';

	// The ⋯ menu of an object, such as a table's row or a card (ADR-0432): every action but the
	// one or two that show as buttons beside it, the destructive ones last. Its name says whose
	// actions they are: "Actions for <name>".
	type Props = {
		items: MenuItem[];
		/** The object's name, for the button's name; without it, "Actions". */
		name?: string;
		size?: 'icon' | 'icon-sm';
		class?: string;
	};

	let { items, name = '', size = 'icon-sm', class: className = '' }: Props = $props();
</script>

<Menu
	{items}
	label={name ? $t('ui.menu.actionsFor', { name }) : $t('ui.menu.actions')}
	icon={MoreHorizontal}
	iconOnly
	variant="ghost"
	{size}
	class={className}
/>
