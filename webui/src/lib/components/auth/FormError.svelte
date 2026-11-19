<script lang="ts">
	// Why the sign-in or the first-run setup failed, above the form's fields.

	type Props = {
		message: string;
	};

	let { message }: Props = $props();
</script>

<div class="bg-status-error/20 border border-status-error text-status-error px-4 py-2 rounded">
	{message}
</div>
