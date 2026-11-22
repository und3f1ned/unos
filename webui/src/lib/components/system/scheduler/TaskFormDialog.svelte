<script lang="ts">
	import { untrack } from 'svelte';
	import {
		createTask,
		updateTask,
		type ScheduledTask,
		type TaskType,
		type ScheduleFrequency
	} from '$lib/api/scheduler';
	import type { Disk, Pool } from '$lib/api/storage';
	import { Alert, Input, Button, Modal, SwitchRow } from '$lib/components/ui';
	import { messageOf } from '$lib/errors';
	import { t } from '$lib/i18n';
	import { DAYS_OF_WEEK, FREQUENCIES, MIN_PASSPHRASE_LENGTH, TASK_TYPES, getDefaultFormData } from './tasks';

	// The dialog that creates or edits a task: drawn while it is open, with a form that starts from
	// the task, or from the defaults with the first pool and disk.
	type Props = {
		// The task being edited; null for a new one.
		task: ScheduledTask | null;
		pools: Pool[];
		disks: Disk[];
		disksLoaded: boolean;
		// The time zone whose clock the schedules follow (the NAS's); empty until it is known.
		timeZone: string;
		onclose: () => void;
		// The task is saved: the dialog is to close and the list to load again.
		onsaved: () => Promise<void>;
	};

	let { task, pools, disks, disksLoaded, timeZone, onclose, onsaved }: Props = $props();

	// What the form starts from, when the dialog opens.
	function initialFormData(): Partial<ScheduledTask> {
		if (task) return { ...task };
		const data = getDefaultFormData();
		if (pools.length > 0) data.target_pool = pools[0].name;
		if (disks.length > 0) data.target_disk = disks[0].path;
		return data;
	}

	let formData = $state<Partial<ScheduledTask>>(untrack(initialFormData));
	// The passphrase of a backup task, typed twice. The API never returns it:
	// the form starts empty, and an edit that leaves it empty keeps the stored one.
	let passphrase = $state('');
	let passphraseConfirm = $state('');
	let saving = $state(false);
	// Why the form was not saved: the name it lacks (by its field), or the server's reason (ADR-0432).
	let nameError = $state('');
	let saveError = $state('');

	// The placeholder of a custom task's command: a path, the same in every language.
	const COMMAND_EXAMPLE = '/usr/local/bin/my-script.sh';

	// A backup task being edited keeps its passphrase when the device has its
	// key. A new one, a task made a backup task, and a backup task without a key
	// (made before backups were encrypted, or its key was lost) need one.
	let keepsPassphrase = $derived(task?.task_type === 'backup' && task.has_backup_key === true);
	let passphraseTooShort = $derived(passphrase !== '' && passphrase.length < MIN_PASSPHRASE_LENGTH);
	let passphraseMismatch = $derived(passphraseConfirm !== passphrase);
	// The hint that names the time zone of the schedule's times, once it is shown.
	let timeZoneHint = $derived(timeZone ? 'task-time-zone-hint' : undefined);
	let waitsForPassphrase = $derived(
		formData.task_type === 'backup' && (passphraseTooShort || passphraseMismatch || (!passphrase && !keepsPassphrase))
	);

	// The disk of the SMART task being edited when the disk list does not have
	// it (not connected, or the list is not loaded): it stays a choice, so
	// saving keeps it. The backend checks the disk again when the task runs.
	let storedDisk = $derived(
		task?.task_type === 'smart_test' && task.target_disk && !disks.some((d) => d.path === task?.target_disk)
			? task.target_disk
			: null
	);

	async function handleSaveTask() {
		saveError = '';
		if (!formData.name?.trim()) {
			nameError = $t('scheduler.form.nameRequired');
			document.getElementById('task-name')?.focus();
			return;
		}
		nameError = '';

		saving = true;
		try {
			const taskData: ScheduledTask = {
				name: formData.name!.trim(),
				task_type: formData.task_type as TaskType,
				enabled: formData.enabled ?? true,
				frequency: formData.frequency as ScheduleFrequency,
				hour: formData.hour ?? 0,
				minute: formData.minute ?? 0,
				day_of_week: formData.day_of_week,
				day_of_month: formData.day_of_month,
				cron_expression: formData.frequency === 'custom' ? formData.cron_expression : undefined,
				target_pool: ['snapshot', 'scrub'].includes(formData.task_type!) ? formData.target_pool : undefined,
				target_disk: formData.task_type === 'smart_test' ? formData.target_disk : undefined,
				command: formData.task_type === 'custom' ? formData.command : undefined,
				keep_count: ['snapshot', 'backup'].includes(formData.task_type!) ? formData.keep_count : undefined,
				// Sent only when typed: left out, the server keeps the stored passphrase.
				passphrase: formData.task_type === 'backup' && passphrase ? passphrase : undefined
			};

			if (task?.id) {
				await updateTask(task.id, taskData);
			} else {
				await createTask(taskData);
			}

			await onsaved();
		} catch (e) {
			saveError = messageOf(e);
		}
		saving = false;
	}
</script>

<!-- The design system's Modal: its header, its close button and Escape, and a dialog
     taller than the window scrolls with its overlay (ADR-0302). -->
<Modal open size="md" title={task ? $t('scheduler.form.editTitle') : $t('scheduler.form.createTitle')} {onclose}>
	<div class="space-y-4">
		<!-- Task Name -->
		<div>
			<label for="task-name" class="form-label">{$t('scheduler.form.name')}</label>
			<input
				id="task-name"
				type="text"
				bind:value={formData.name}
				placeholder={$t('scheduler.form.namePlaceholder')}
				class="form-input"
				class:border-status-error={nameError}
				aria-invalid={nameError ? 'true' : undefined}
				aria-describedby={nameError ? 'task-name-error' : undefined}
				oninput={() => (nameError = '')}
			/>
			{#if nameError}
				<p id="task-name-error" class="form-error">{nameError}</p>
			{/if}
		</div>

		<!-- Task Type -->
		<div>
			<label for="task-type" class="form-label">{$t('scheduler.form.type')}</label>
			<select id="task-type" bind:value={formData.task_type} class="form-select">
				{#each TASK_TYPES as type}
					<option value={type.value}>{$t(type.key)}</option>
				{/each}
			</select>
		</div>

		<!-- Pool Selection (for snapshot/scrub) -->
		{#if formData.task_type === 'snapshot' || formData.task_type === 'scrub'}
			<div>
				<label for="task-target-pool" class="form-label">{$t('scheduler.form.pool')}</label>
				{#if pools.length === 0}
					<p class="text-sm text-status-warning">{$t('scheduler.form.noPools')}</p>
				{:else}
					<select id="task-target-pool" bind:value={formData.target_pool} class="form-select">
						{#each pools as pool}
							<option value={pool.name}>{pool.name}</option>
						{/each}
					</select>
				{/if}
			</div>
		{/if}

		<!-- Disk Selection (for smart_test) -->
		{#if formData.task_type === 'smart_test'}
			<div>
				<label for="task-target-disk" class="form-label">{$t('scheduler.form.disk')}</label>
				{#if disks.length === 0 && !storedDisk}
					<p class="text-sm text-status-warning">{$t('scheduler.form.noDisks')}</p>
				{:else}
					<!-- Made again when the extra option goes (the disk list arrived and has
                 the disk): Svelte selects the bound value when it changes, not when
                 the options change, and the browser would show the first disk. -->
					{#key storedDisk}
						<select id="task-target-disk" bind:value={formData.target_disk} class="form-select">
							{#if storedDisk}
								<option value={storedDisk}
									>{disksLoaded ? $t('scheduler.form.notConnected', { disk: storedDisk }) : storedDisk}</option
								>
							{/if}
							{#each disks as disk}
								<option value={disk.path}>{disk.path} - {disk.model || disk.name}</option>
							{/each}
						</select>
					{/key}
				{/if}
			</div>
		{/if}

		<!-- Off-device copy: its target and folders are those of System > Backup & Restore (ADR-0337) -->
		{#if formData.task_type === 'offsite_copy'}
			<p class="text-xs text-content-muted">{$t('scheduler.form.offsiteHint')}</p>
		{/if}

		<!-- Custom Command -->
		{#if formData.task_type === 'custom'}
			<div>
				<label for="task-command" class="form-label">{$t('scheduler.form.command')}</label>
				<input
					id="task-command"
					type="text"
					bind:value={formData.command}
					placeholder={COMMAND_EXAMPLE}
					aria-describedby="task-command-hint"
					class="form-input font-mono text-sm"
				/>
				<p id="task-command-hint" class="text-xs text-content-muted mt-1">{$t('scheduler.form.commandHint')}</p>
			</div>
		{/if}

		<!-- Keep Count (for snapshot/backup): a count that steps by one, with Input's steppers
		     (ADR-0432). Input's value is the text typed; the task's a number, or none. -->
		{#if formData.task_type === 'snapshot' || formData.task_type === 'backup'}
			<Input
				id="task-keep-count"
				type="number"
				label={$t('scheduler.form.keepCount')}
				bind:value={
					() => String(formData.keep_count ?? ''),
					(typed) => (formData.keep_count = typed === '' ? undefined : Number(typed))
				}
				min={1}
				max={100}
				width="sm"
				steppers
				hint={formData.task_type === 'backup' ? $t('scheduler.form.keepBackups') : $t('scheduler.form.keepSnapshots')}
			/>
		{/if}

		<!-- Passphrase (for backup): the backups are encrypted with it -->
		{#if formData.task_type === 'backup'}
			<Input
				id="task-passphrase"
				type="password"
				label={$t('scheduler.form.passphrase')}
				autocomplete="new-password"
				bind:value={passphrase}
				error={passphraseTooShort ? $t('scheduler.form.passphraseTooShort', { n: MIN_PASSPHRASE_LENGTH }) : ''}
				hint={keepsPassphrase
					? $t('scheduler.form.passphraseKept')
					: $t('scheduler.form.passphraseNeeded', { n: MIN_PASSPHRASE_LENGTH })}
			/>
			<Input
				id="task-passphrase-confirm"
				type="password"
				label={$t('scheduler.form.confirmPassphrase')}
				autocomplete="new-password"
				bind:value={passphraseConfirm}
				error={passphraseConfirm && passphraseMismatch ? $t('scheduler.form.passphraseMismatch') : ''}
				hint={$t('scheduler.form.typeAgain')}
			/>
		{/if}

		<hr class="border-border" />

		<!-- Schedule Frequency -->
		<div>
			<label for="task-frequency" class="form-label">{$t('scheduler.form.frequency')}</label>
			<select id="task-frequency" bind:value={formData.frequency} class="form-select">
				{#each FREQUENCIES as freq}
					<option value={freq.value}>{$t(freq.key)}</option>
				{/each}
			</select>
		</div>

		<!-- Custom Cron Expression -->
		{#if formData.frequency === 'custom'}
			<div>
				<label for="task-cron-expression" class="form-label">{$t('scheduler.form.cron')}</label>
				<input
					id="task-cron-expression"
					type="text"
					bind:value={formData.cron_expression}
					placeholder="0 2 * * *"
					aria-describedby={timeZoneHint}
					class="form-input font-mono"
				/>
				<p class="text-xs text-content-muted mt-1">{$t('scheduler.form.cronFormat')}</p>
			</div>
		{:else}
			<!-- The time as one group, hour and minute side by side on the NAS's clock. -->
			<fieldset>
				<legend class="form-label">{$t('scheduler.form.time')}</legend>
				<div class="flex items-center gap-2">
					{#if formData.frequency !== 'hourly'}
						<label for="task-hour" class="sr-only">{$t('scheduler.form.hour')}</label>
						<select
							id="task-hour"
							aria-describedby={timeZoneHint}
							bind:value={formData.hour}
							class="form-select w-24 tabular"
						>
							{#each Array(24), i}
								<!-- design-ignore: the hour field's values, 00 to 23, as the 24-hour clock of the NAS's schedules -->
								<option value={i}>{String(i).padStart(2, '0')}</option>
							{/each}
						</select>
						<span class="text-content-secondary" aria-hidden="true">:</span>
					{:else}
						<span class="text-sm text-content-secondary">{$t('scheduler.form.everyHourAt')}</span>
					{/if}
					<label for="task-minute" class="sr-only">{$t('scheduler.form.minute')}</label>
					<select
						id="task-minute"
						aria-describedby={timeZoneHint}
						bind:value={formData.minute}
						class="form-select w-24 tabular"
					>
						{#each [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55] as m}
							<!-- design-ignore: the minute field's values, 00 to 55 -->
							<option value={m}>{String(m).padStart(2, '0')}</option>
						{/each}
					</select>
				</div>
			</fieldset>

			<!-- Day Selection -->
			{#if formData.frequency === 'weekly'}
				<div>
					<label for="task-day-of-week" class="form-label">{$t('scheduler.form.dayOfWeek')}</label>
					<select
						id="task-day-of-week"
						aria-describedby={timeZoneHint}
						bind:value={formData.day_of_week}
						class="form-select"
					>
						{#each DAYS_OF_WEEK as day}
							<option value={day.value}>{$t(day.key)}</option>
						{/each}
					</select>
				</div>
			{/if}

			{#if formData.frequency === 'monthly'}
				<div>
					<label for="task-day-of-month" class="form-label">{$t('scheduler.form.dayOfMonth')}</label>
					<select
						id="task-day-of-month"
						aria-describedby={timeZoneHint}
						bind:value={formData.day_of_month}
						class="form-select"
					>
						{#each Array(28), i}
							<option value={i + 1}>{i + 1}</option>
						{/each}
					</select>
				</div>
			{/if}
		{/if}
		{#if timeZone}
			<p id="task-time-zone-hint" class="text-xs text-content-muted">
				{$t('scheduler.form.onTheClock', { zone: timeZone })}
			</p>
		{/if}

		<!-- Enabled: a switch (ADR-0432) -->
		<SwitchRow id="task-enabled" title={$t('scheduler.task.enable')} bind:checked={formData.enabled} />

		{#if saveError}
			<div role="alert">
				<Alert variant="error" size="sm" title={$t('scheduler.form.saveFailed')}>{saveError}</Alert>
			</div>
		{/if}
	</div>

	{#snippet footer()}
		<Button variant="secondary" class="flex-1" onclick={onclose}>
			{$t('common.cancel')}
		</Button>
		<Button
			variant="primary"
			class="flex-1"
			onclick={handleSaveTask}
			loading={saving}
			disabled={waitsForPassphrase}
			disabledReason={waitsForPassphrase ? $t('scheduler.form.passphraseWait') : ''}
		>
			{task ? $t('scheduler.form.save') : $t('scheduler.form.create')}
		</Button>
	{/snippet}
</Modal>
