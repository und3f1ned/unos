<script lang="ts">
	import { Alert } from '$lib/components/ui';
	// Why the sign-in or the first-run setup failed, above the form's fields.

	type Props = {
		message: string;
	};

	let { message }: Props = $props();
</script>

<!-- An error as an Alert (ADR-0428). -->
<Alert variant="error" size="sm">{message}</Alert>
