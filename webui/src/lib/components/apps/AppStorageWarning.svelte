<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import type { InstalledApp } from '$lib/api/apps';

	/** Installed app whose data folders are checked; nothing is shown when all are on mounted pools. */
	export let app: InstalledApp;
</script>

{#if app.data_outside_pools.length > 0}
	<Alert
		variant="warning"
		class={$$props.class || ''}
		title={app.pool ? 'Data folders are not available' : 'App data is on the system disk'}
	>
		{#if app.pool}
			<p>
				Some data folders of {app.name} are not on a mounted storage pool. Mount the pool that holds
				them on the Storage page, then start the app.
			</p>
		{:else}
			<p>
				{app.name} was installed by an older UNOS version and keeps its data on the system disk instead
				of a storage pool, so it fills the system disk. UNOS does not start it again. To move it,
				uninstall the app (its data is kept), move these folders to a pool, then install the app on
				that pool.
			</p>
		{/if}
		<ul class="mt-2 space-y-1">
			{#each app.data_outside_pools as path}
				<li class="mono text-xs break-all">{path}</li>
			{/each}
		</ul>
	</Alert>
{/if}
