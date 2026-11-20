<script lang="ts">
	import { t } from '$lib/i18n';
	import { categoryLabel } from './labels';

	type Props = {
		/** A category as the server answers it (AppCategory of supervisor/src/apps/schemas.py); '' is every app. */
		category: string;
	};

	let { category }: Props = $props();

</script>

<!-- A category without a label is shown as the server answers it. -->
{categoryLabel(category, $t)}
