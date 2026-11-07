<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { Wrench } from 'lucide-svelte';
	import { ApiError } from '$lib/api/client';
	import { getRepairResult, repairSharePermissions, type RepairResult } from '$lib/api/shares';
	import { Alert, Badge, Button, Modal } from '$lib/components/ui';

	/** The share whose folder is repaired. The dialog is open while mounted. */
	export let name: string;

	const dispatch = createEventDispatcher<{ close: void; repaired: RepairResult }>();

	let result: RepairResult | null = null;
	let running = false;
	let error = '';
	let lastError = '';

	const stateBadges: Record<
		RepairResult['state'],
		{ variant: 'success' | 'warning' | 'error'; label: string }
	> = {
		done: { variant: 'success', label: 'Done' },
		incomplete: { variant: 'warning', label: 'Incomplete' },
		failed: { variant: 'error', label: 'Failed' }
	};

	const skippedLabels: Record<string, string> = {
		other_filesystem: 'other volumes and mounts',
		snapshots: 'snapshots',
		symlink: 'links',
		special: 'special files',
		hardlink: 'hard-linked files',
		private: 'private files and folders',
		too_deep: 'too deep',
		raced: 'changed while running'
	};

	onMount(async () => {
		// The last run since UNOS started, if any (404: none).
		try {
			result = await getRepairResult(name);
		} catch (e) {
			if (!(e instanceof ApiError && e.status === 404)) lastError = messageOf(e);
		}
	});

	function messageOf(e: unknown): string {
		return e instanceof Error ? e.message : String(e);
	}

	async function repair() {
		if (running) return;
		running = true;
		error = '';
		try {
			result = await repairSharePermissions(name);
			dispatch('repaired', result);
		} catch (e) {
			// 409: not served, pool not mounted or read-only, group problem, already running.
			error = messageOf(e);
		} finally {
			running = false;
		}
	}

	function close() {
		if (!running) dispatch('close');
	}

	function formatTime(value: string): string {
		const date = new Date(value);
		return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
	}

	$: skippedCounts = result
		? Object.entries(result.skipped)
				.filter(([, count]) => count > 0)
				.map(([key, count]) => `${skippedLabels[key] ?? key}: ${count}`)
		: [];
</script>

<Modal open title="Repair permissions of {name}" size="lg" closable={!running} on:close={close}>
	<div class="space-y-3 max-h-[60vh] overflow-y-auto scrollbar-thin pr-1">
		<!-- The outcome first: after a run it is what the user is waiting for. -->
		{#if lastError}
			<Alert variant="error" title="The last result could not be read">{lastError}</Alert>
		{/if}

		{#if error}
			<Alert variant="error" title="Permissions were not repaired">{error}</Alert>
		{/if}

		{#if running}
			<p class="text-sm text-secondary">Repairing... This can take a few minutes.</p>
		{/if}

		{#if result}
			{@const badge = stateBadges[result.state] ?? { variant: 'warning', label: result.state }}
			<section class="card-nested space-y-2 text-sm" aria-label="Repair result">
				<div class="flex flex-wrap items-center gap-2">
					<span class="font-medium">Last run</span>
					<Badge variant={badge.variant}>{badge.label}</Badge>
					<span class="text-xs text-muted">{formatTime(result.finished_at)}</span>
				</div>
				<p>
					{result.visited} checked, {result.changed} changed, {result.errors}
					{result.errors === 1 ? 'error' : 'errors'}
				</p>
				{#if skippedCounts.length > 0}
					<p>Skipped: {skippedCounts.join(', ')}</p>
				{/if}
				{#if result.skipped_paths.length > 0}
					<div>
						<p>Skipped volumes and mount points:</p>
						<ul class="ml-5 list-disc">
							{#each result.skipped_paths as path}
								<li class="mono-xs break-all">{path}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if result.error_samples.length > 0}
					<div>
						<p>Errors:</p>
						<ul class="ml-5 list-disc">
							{#each result.error_samples as sample}
								<li class="mono-xs break-all">{sample}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if result.message}
					{#if result.state === 'done'}
						<p>{result.message}</p>
					{:else}
						<Alert variant={result.state === 'failed' ? 'error' : 'warning'}>{result.message}</Alert>
					{/if}
				{/if}
			</section>
		{/if}

		<p class="text-sm">
			Repair sets up the existing files and folders of this share for SMB users, as UNOS does for
			new share folders:
		</p>
		<ul class="list-disc ml-5 text-sm space-y-1">
			<li>
				They get the group unos-smb, which can read and write them. Folders also keep new files in
				the group.
			</li>
			<li>
				Other accounts lose access, including apps that run as their own user (for example a
				download or media app). Repair does not change ACL entries (set with setfacl): users and
				groups named there keep their access and can get more, and on such files the group
				unos-smb gets only what the ACL gives the owning group.
			</li>
			<li>Owners and file contents do not change.</li>
			<li>Private files and folders (only their owner can use them) are left alone.</li>
			<li>Snapshots, other volumes, mount points and links are skipped.</li>
			<li>
				It can take minutes on a large share, and stops after 4 minutes: run it again to continue.
			</li>
		</ul>
		<Alert variant="warning">
			This changes the permissions of existing files. It cannot be undone automatically.
		</Alert>
	</div>

	<svelte:fragment slot="footer">
		<Button variant="secondary" class="flex-1" disabled={running} on:click={close}>Close</Button>
		<Button variant="warning" class="flex-1" icon={Wrench} loading={running} on:click={repair}>
			Repair permissions
		</Button>
	</svelte:fragment>
</Modal>
