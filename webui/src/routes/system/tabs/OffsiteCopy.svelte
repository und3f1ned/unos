<script lang="ts">
  // The off-device copy (ADR-0337), a section of System > Backup & Restore: the copy target with its
  // password, the folders to copy, the state of the last copy and restore with Run now, a daily task
  // of the Scheduler, and the snapshots with their restores. The state is read when the section
  // opens and on Refresh: copies and restores run in the background, and nothing polls (ADR-0006).
  import { createEventDispatcher, onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import { formatBytes } from "$lib/api/backup";
  import {
    getOffsiteSettings,
    getOffsiteSnapshots,
    getOffsiteStatus,
    removeOffsiteSettings,
    restoreOffsiteConfiguration,
    restoreOffsiteFiles,
    runOffsiteCopy,
    saveOffsiteSettings,
    type OffsiteKind,
    type OffsiteSettingsAnswer,
    type OffsiteSettingsRequest,
    type OffsiteSnapshot,
    type OffsiteStatus,
  } from "$lib/api/offsite";
  import { createTask, getTasks, type ScheduledTask } from "$lib/api/scheduler";
  import { Alert, Badge, Button, Card, Input } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { Play, Plus, RefreshCw, RotateCcw, Trash2, UploadCloud, X } from "lucide-svelte";

  const dispatch = createEventDispatcher<{ restored: string }>();

  // At least as long as the server asks (MIN_PASSWORD_LENGTH of supervisor/src/offsite/service.py).
  const MIN_PASSWORD_LENGTH = 12;

  // What the server names as words, with the keys of their texts (ADR-0287).
  const STEPS: Record<string, string> = {
    settings: "offsite.step.settings",
    folders: "offsite.step.folders",
    unlock: "offsite.step.unlock",
    configuration: "offsite.step.configuration",
    data: "offsite.step.data",
    forget: "offsite.step.forget",
    check: "offsite.step.check",
    restore_test: "offsite.step.restore_test",
  };
  const WARNINGS: Record<string, string> = { same_disk: "offsite.warning.sameDisk" };
  const SNAPSHOT_KINDS: Record<string, string> = {
    configuration: "offsite.snapshots.configuration",
    data: "offsite.snapshots.data",
    other: "offsite.snapshots.other",
  };

  let loading = true;
  let answer: OffsiteSettingsAnswer | null = null;
  let status: OffsiteStatus | null = null;
  let snapshots: OffsiteSnapshot[] = [];
  let snapshotsError = "";
  let tasks: ScheduledTask[] = [];
  // The last failed action, until it is dismissed; and what the last action did.
  let error: { title: string; message: string } | null = null;
  let errorBox: HTMLDivElement | undefined;
  let notice = "";

  // The form.
  let kind: OffsiteKind = "disk";
  let path = "";
  let endpoint = "";
  let bucket = "";
  let prefix = "";
  let region = "";
  let accessKeyId = "";
  let secretKey = "";
  let poolSources: string[] = [];
  let extraSources: string[] = [];
  let newSource = "";
  let password = "";
  let passwordAgain = "";
  let limitUpload = "0";
  let limitDownload = "0";
  let keepDaily = "7";
  let keepWeekly = "4";
  let keepMonthly = "12";
  let checkPercent = "2";
  let saving = false;
  let starting = false;
  let addingTask = false;
  let restoring: string | null = null;
  // The snapshot whose Restore files form is open, with its pool and folder.
  let filesOf: string | null = null;
  let restorePool = "";
  let restorePath = "";

  $: stored = answer?.settings ?? null;
  $: sources = [...poolSources, ...extraSources];
  // An edit of the same target keeps the stored password and key (the server's rule).
  $: sameTarget =
    stored !== null &&
    stored.kind === kind &&
    (kind === "disk"
      ? normalized(path) === stored.path
      : endpoint.trim() === stored.endpoint && bucket.trim() === stored.bucket && (prefix.trim() || null) === (stored.prefix ?? null));
  $: keepsPassword = !!answer?.has_password && sameTarget;
  $: keepsSecret =
    !!answer?.has_s3_secret_access_key &&
    stored?.kind === "s3" &&
    kind === "s3" &&
    endpoint.trim() === stored.endpoint &&
    accessKeyId.trim() === stored.access_key_id;
  $: passwordTooShort = password !== "" && password.length < MIN_PASSWORD_LENGTH;
  $: passwordsDiffer = passwordAgain !== password;
  $: targetGiven = kind === "disk" ? path.trim() !== "" : !!(endpoint.trim() && bucket.trim() && accessKeyId.trim() && (secretKey || keepsSecret));
  $: canSave =
    !saving &&
    targetGiven &&
    sources.length > 0 &&
    !passwordTooShort &&
    !passwordsDiffer &&
    (password !== "" || keepsPassword);
  // The pools a restore of files may go into: the managed ones and those of the folders copied.
  $: restorePools = [
    ...new Set([
      ...(answer?.pools ?? []).map((pool) => pool.name),
      ...sources.map((source) => poolOf(source)).filter((name): name is string => !!name),
    ]),
  ].sort();
  $: running = status?.running ?? null;
  $: lastCopy = status?.last_copy ?? null;
  $: lastRestore = status?.last_restore ?? null;
  // The units of sizes in the language of the page (formatBytes).
  $: sizeUnits = [
    $t("backup.size.bytes"),
    $t("backup.size.kilobytes"),
    $t("backup.size.megabytes"),
    $t("backup.size.gigabytes"),
  ];

  function normalized(value: string): string {
    return "/" + value.split("/").filter((part) => part !== "" && part !== ".").join("/");
  }

  /** The pool of a folder below the pools' base, or null. */
  function poolOf(source: string): string | null {
    const base = answer?.pools_base;
    if (!base || !source.startsWith(base + "/")) return null;
    return source.slice(base.length + 1).split("/")[0] || null;
  }

  $: when = (iso: string | null | undefined): string => (iso ? new Date(iso).toLocaleString($intlLocale) : "");
  $: stepText = (step: string | null): string =>
    step && Object.prototype.hasOwnProperty.call(STEPS, step) ? $t(STEPS[step]) : (step ?? "");
  $: warningText = (warning: { kind: string; message: string }): string =>
    Object.prototype.hasOwnProperty.call(WARNINGS, warning.kind) ? $t(WARNINGS[warning.kind]) : warning.message;
  $: kindText = (snapshotKind: string): string =>
    Object.prototype.hasOwnProperty.call(SNAPSHOT_KINDS, snapshotKind) ? $t(SNAPSHOT_KINDS[snapshotKind]) : snapshotKind;
  $: runningText = (activity: NonNullable<OffsiteStatus["running"]>): string => {
    const time = when(activity.started);
    if (activity.kind === "copy") return $t("offsite.running.copy", { time });
    if (activity.kind === "restore-files") return $t("offsite.running.restoreFiles", { folder: activity.folder ?? "", time });
    if (activity.kind === "restore-configuration") return $t("offsite.running.restoreConfiguration", { time });
    return $t("offsite.running.settings");
  };

  function sessionEnded(e: unknown): boolean {
    return e instanceof ApiError && e.status === 401;
  }

  async function showError(title: string, e: unknown) {
    if (sessionEnded(e)) return;
    error = { title, message: e instanceof Error ? e.message : String(e) };
    await tick();
    errorBox?.scrollIntoView({ block: "nearest" });
  }

  /** The form from the stored settings, or the defaults: every managed pool, the retention of restic's. */
  function fillForm(found: OffsiteSettingsAnswer) {
    const settings = found.settings;
    const poolPaths = found.pools.map((pool) => pool.path);
    kind = settings?.kind ?? "disk";
    path = settings?.path ?? "";
    endpoint = settings?.endpoint ?? "";
    bucket = settings?.bucket ?? "";
    prefix = settings?.prefix ?? "";
    region = settings?.region ?? "";
    accessKeyId = settings?.access_key_id ?? "";
    poolSources = settings ? settings.sources.filter((source) => poolPaths.includes(source)) : poolPaths;
    extraSources = settings ? settings.sources.filter((source) => !poolPaths.includes(source)) : [];
    limitUpload = String(settings?.limit_upload_kib ?? 0);
    limitDownload = String(settings?.limit_download_kib ?? 0);
    keepDaily = String(settings?.keep_daily ?? 7);
    keepWeekly = String(settings?.keep_weekly ?? 4);
    keepMonthly = String(settings?.keep_monthly ?? 12);
    checkPercent = String(settings?.check_percent ?? 2);
    password = passwordAgain = secretKey = "";
  }

  async function loadTasks() {
    try {
      tasks = (await getTasks()).filter((task) => task.task_type === "offsite_copy");
    } catch (e) {
      tasks = [];
    }
  }

  async function loadSnapshots() {
    snapshotsError = "";
    try {
      snapshots = (await getOffsiteSnapshots()).snapshots;
    } catch (e) {
      snapshots = [];
      if (!sessionEnded(e)) snapshotsError = e instanceof Error ? e.message : String(e);
    }
  }

  async function load() {
    loading = true;
    try {
      const [found, state] = await Promise.all([getOffsiteSettings(), getOffsiteStatus()]);
      answer = found;
      status = state;
      fillForm(found);
      await Promise.all([loadTasks(), found.configured ? loadSnapshots() : Promise.resolve()]);
    } catch (e) {
      showError($t("offsite.loadFailed"), e);
    }
    loading = false;
  }

  async function refresh() {
    error = null;
    try {
      status = await getOffsiteStatus();
      await Promise.all([loadTasks(), answer?.configured ? loadSnapshots() : Promise.resolve()]);
    } catch (e) {
      showError($t("offsite.loadFailed"), e);
    }
  }

  onMount(load);

  function addSource() {
    const value = newSource.trim();
    if (value && !sources.includes(value)) extraSources = [...extraSources, value];
    newSource = "";
  }

  function removeSource(source: string) {
    extraSources = extraSources.filter((other) => other !== source);
  }

  async function save() {
    saving = true;
    error = null;
    notice = "";
    const request: OffsiteSettingsRequest = {
      kind,
      path: kind === "disk" ? path.trim() : null,
      endpoint: kind === "s3" ? endpoint.trim() : null,
      bucket: kind === "s3" ? bucket.trim() : null,
      prefix: kind === "s3" ? prefix.trim() || null : null,
      region: kind === "s3" ? region.trim() || null : null,
      access_key_id: kind === "s3" ? accessKeyId.trim() : null,
      sources,
      limit_upload_kib: Number(limitUpload),
      limit_download_kib: Number(limitDownload),
      keep_daily: Number(keepDaily),
      keep_weekly: Number(keepWeekly),
      keep_monthly: Number(keepMonthly),
      check_percent: Number(checkPercent),
      // Sent only when typed: left out, the server keeps the stored ones of the same target.
      password: password || undefined,
      s3_secret_access_key: kind === "s3" && secretKey ? secretKey : undefined,
    };
    try {
      answer = await saveOffsiteSettings(request);
      fillForm(answer);
      notice = $t("offsite.form.saved");
      await refresh();
    } catch (e) {
      showError($t("offsite.form.saveFailed"), e);
    }
    saving = false;
  }

  async function forget() {
    if (!confirm($t("offsite.form.confirmForget"))) return;
    error = null;
    notice = "";
    try {
      await removeOffsiteSettings();
      snapshots = [];
      await load();
    } catch (e) {
      showError($t("offsite.form.forgetFailed"), e);
    }
  }

  async function runNow() {
    starting = true;
    error = null;
    try {
      await runOffsiteCopy();
      notice = $t("offsite.state.runStarted");
      status = await getOffsiteStatus();
    } catch (e) {
      showError($t("offsite.state.runFailed"), e);
    }
    starting = false;
  }

  async function addDailyTask() {
    addingTask = true;
    error = null;
    try {
      await createTask({
        name: $t("offsite.task.name"),
        task_type: "offsite_copy",
        enabled: true,
        frequency: "daily",
        hour: 3,
        minute: 0,
      });
      await loadTasks();
    } catch (e) {
      showError($t("offsite.task.addFailed"), e);
    }
    addingTask = false;
  }

  async function restoreConfiguration(snapshot: OffsiteSnapshot) {
    restoring = snapshot.id;
    error = null;
    notice = "";
    try {
      const result = await restoreOffsiteConfiguration(snapshot.id);
      notice = $t("offsite.snapshots.inBackupList", { name: result.backup.filename });
      dispatch("restored", result.backup.filename);
    } catch (e) {
      showError($t("offsite.snapshots.restoreConfigurationFailed"), e);
    }
    restoring = null;
  }

  function openFilesForm(snapshot: OffsiteSnapshot) {
    filesOf = snapshot.id;
    restorePool = restorePools[0] ?? "";
    restorePath = "";
  }

  async function restoreFiles(snapshot: OffsiteSnapshot) {
    restoring = snapshot.id;
    error = null;
    notice = "";
    try {
      const result = await restoreOffsiteFiles(snapshot.id, restorePool, restorePath.trim() || undefined);
      notice = $t("offsite.snapshots.restoring", { folder: result.folder });
      filesOf = null;
      status = await getOffsiteStatus();
    } catch (e) {
      showError($t("offsite.snapshots.restoreFilesFailed"), e);
    }
    restoring = null;
  }
</script>

<section aria-labelledby="offsite-title">
  <Card>
    <h3 id="offsite-title" class="section-title mb-2">
      <UploadCloud class="w-5 h-5" />
      {$t("offsite.title")}
    </h3>
    <p class="text-sm text-content-secondary mb-4">{$t("offsite.intro")}</p>

    {#if error}
      <!-- role="alert": screen readers announce the message when it appears. -->
      <div bind:this={errorBox} role="alert" class="mb-4">
        <Alert variant="error" title={error.title} dismissible on:dismiss={() => (error = null)}>
          {error.message}
        </Alert>
      </div>
    {/if}
    {#if notice}
      <div role="status" class="mb-4">
        <Alert variant="success" dismissible on:dismiss={() => (notice = "")}>{notice}</Alert>
      </div>
    {/if}

    {#if loading}
      <p class="text-sm text-content-muted">{$t("common.loading")}</p>
    {:else if answer}
      {#if !answer.configured}
        <p class="text-sm mb-4">{$t("offsite.notSetUp")}</p>
      {:else}
        {#each answer.warnings as warning}
          <Alert variant="warning" class="mb-4">{warningText(warning)}</Alert>
        {/each}

        <!-- The state: what runs, the last copy and the last restore. -->
        <div class="card-nested p-3 mb-4 space-y-2 text-sm">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="font-medium">{$t("offsite.state.title")}</h4>
            <div class="flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" icon={RefreshCw} on:click={refresh}>{$t("offsite.refresh")}</Button>
              <Button size="sm" icon={Play} loading={starting} disabled={running !== null} on:click={runNow}>
                {$t("offsite.state.runNow")}
              </Button>
            </div>
          </div>
          {#if running}
            <p class="text-status-info">{runningText(running)}</p>
          {/if}
          {#if lastCopy}
            <p class="flex flex-wrap items-center gap-2">
              <span class={lastCopy.success ? "text-status-success" : "text-status-error"}>
                {lastCopy.success ? $t("offsite.state.succeeded") : $t("offsite.state.failed")}
              </span>
              <span class="text-content-muted">
                {$t("offsite.state.started", { time: when(lastCopy.started) })},
                {lastCopy.trigger === "task" ? $t("offsite.state.byTask") : $t("offsite.state.byHand")}
              </span>
            </p>
            {#if lastCopy.summary}
              <p>
                {$t("offsite.state.files", {
                  new: lastCopy.summary.files_new,
                  changed: lastCopy.summary.files_changed,
                  unmodified: lastCopy.summary.files_unmodified,
                  added: formatBytes(lastCopy.summary.bytes_added, $intlLocale, sizeUnits),
                })}
              </p>
            {/if}
            <p class="flex flex-wrap gap-x-4">
              <span>
                {lastCopy.check === "passed"
                  ? $t("offsite.state.checkPassed")
                  : lastCopy.check === "failed"
                    ? $t("offsite.state.checkFailed")
                    : $t("offsite.state.checkNotRun")}
              </span>
              <span>
                {lastCopy.restore_test === "passed"
                  ? $t("offsite.state.restoreTestPassed")
                  : lastCopy.restore_test === "failed"
                    ? $t("offsite.state.restoreTestFailed")
                    : $t("offsite.state.restoreTestNotRun")}
              </span>
            </p>
            {#if lastCopy.error}
              <p class="text-status-error break-words">
                {$t("offsite.state.failedAt", { step: stepText(lastCopy.step) })}
                {lastCopy.error}
              </p>
            {/if}
            {#if lastCopy.note}
              <p class="text-status-warning break-words" data-testid="copy-note">{lastCopy.note}</p>
            {/if}
          {:else}
            <p>{$t("offsite.state.never")}</p>
          {/if}
          {#if lastRestore}
            <p class="break-all">
              <span class={lastRestore.success ? "text-status-success" : "text-status-error"}>
                {lastRestore.success ? $t("offsite.restore.succeeded") : $t("offsite.restore.failed")}
              </span>
              <span class="font-mono text-xs" data-testid="restore-folder">{lastRestore.folder}</span>
            </p>
            {#if lastRestore.error}
              <p class="text-status-error break-words">{lastRestore.error}</p>
            {/if}
          {/if}

          <!-- The task of the Scheduler that runs the copy. -->
          {#if tasks.length === 0}
            <div class="flex flex-wrap items-center gap-2 pt-1">
              <span class="text-status-warning">{$t("offsite.task.none")}</span>
              <Button size="sm" variant="secondary" icon={Plus} loading={addingTask} on:click={addDailyTask}>
                {$t("offsite.task.add")}
              </Button>
            </div>
          {:else}
            <p class="pt-1 break-words">
              {$t("offsite.task.some", { names: tasks.map((task) => task.name).join(", ") })}
              {#if tasks.find((task) => task.enabled && task.next_run)}
                {$t("offsite.task.next", { time: when(tasks.find((task) => task.enabled && task.next_run)?.next_run) })}
              {/if}
            </p>
          {/if}
        </div>

        <!-- The snapshots, with their restores. -->
        <h4 class="font-medium text-sm mb-2">{$t("offsite.snapshots.title")}</h4>
        {#if snapshotsError}
          <p class="text-sm text-status-error mb-4 break-words">{$t("offsite.snapshots.loadFailed")}: {snapshotsError}</p>
        {:else if snapshots.length === 0}
          <p class="text-sm text-content-muted mb-4">{$t("offsite.snapshots.empty")}</p>
        {:else}
          <ul class="space-y-2 mb-4" aria-label={$t("offsite.snapshots.label")}>
            {#each snapshots as snapshot (snapshot.id)}
              <li class="card-nested p-3 text-sm">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <Badge variant={snapshot.kind === "configuration" ? "info" : "neutral"}>{kindText(snapshot.kind)}</Badge>
                    <span>{when(snapshot.time)}</span>
                    <span class="font-mono text-xs text-content-muted">{snapshot.short_id}</span>
                    {#if snapshot.size !== null}
                      <span class="text-content-muted">{formatBytes(snapshot.size, $intlLocale, sizeUnits)}</span>
                    {/if}
                    {#if snapshot.files !== null && snapshot.kind !== "configuration"}
                      <span class="text-content-muted">{$t("offsite.snapshots.files", { n: snapshot.files })}</span>
                    {/if}
                  </div>
                  <div class="flex flex-wrap gap-2">
                    {#if snapshot.kind === "configuration"}
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={RotateCcw}
                        loading={restoring === snapshot.id}
                        disabled={restoring !== null || running !== null}
                        on:click={() => restoreConfiguration(snapshot)}
                      >
                        {$t("offsite.snapshots.restoreConfiguration")}
                      </Button>
                    {:else}
                      <Button
                        size="sm"
                        variant="secondary"
                        icon={RotateCcw}
                        disabled={restoring !== null || running !== null}
                        on:click={() => openFilesForm(snapshot)}
                      >
                        {$t("offsite.snapshots.restoreFiles")}
                      </Button>
                    {/if}
                  </div>
                </div>
                <p class="mt-1 font-mono text-xs text-content-secondary break-all">{snapshot.paths.join(", ")}</p>
                {#if filesOf === snapshot.id}
                  <form class="mt-3 space-y-3" on:submit|preventDefault={() => restoreFiles(snapshot)}>
                    <div>
                      <label for="offsite-restore-pool-{snapshot.short_id}" class="form-label">{$t("offsite.snapshots.pool")}</label>
                      <select id="offsite-restore-pool-{snapshot.short_id}" class="form-input" bind:value={restorePool}>
                        {#each restorePools as pool}
                          <option value={pool}>{pool}</option>
                        {/each}
                      </select>
                    </div>
                    <Input
                      id="offsite-restore-path-{snapshot.short_id}"
                      label={$t("offsite.snapshots.folder")}
                      bind:value={restorePath}
                      hint={$t("offsite.snapshots.folderHint", { path: snapshot.paths[0] ?? "/" })}
                    />
                    <p class="form-hint">{$t("offsite.snapshots.into")}</p>
                    <div class="flex flex-wrap gap-2">
                      <Button type="submit" size="sm" loading={restoring === snapshot.id} disabled={!restorePool || restoring !== null}>
                        {$t("offsite.snapshots.restore")}
                      </Button>
                      <Button size="sm" variant="secondary" icon={X} on:click={() => (filesOf = null)}>
                        {$t("common.cancel")}
                      </Button>
                    </div>
                  </form>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}

      <!-- The target and what is copied: saving tests the target. -->
      <form class="space-y-4" on:submit|preventDefault={save}>
        <h4 class="font-medium text-sm">{$t("offsite.form.title")}</h4>
        <fieldset class="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          <legend class="sr-only">{$t("offsite.form.kind")}</legend>
          <label class="flex items-center gap-2">
            <input type="radio" name="offsite-kind" value="disk" bind:group={kind} />
            {$t("offsite.form.disk")}
          </label>
          <label class="flex items-center gap-2">
            <input type="radio" name="offsite-kind" value="s3" bind:group={kind} />
            {$t("offsite.form.s3")}
          </label>
        </fieldset>

        {#if kind === "disk"}
          <Input id="offsite-path" label={$t("offsite.form.path")} bind:value={path} hint={$t("offsite.form.pathHint")} spellcheck={false} />
        {:else}
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input id="offsite-endpoint" label={$t("offsite.form.endpoint")} bind:value={endpoint} hint={$t("offsite.form.endpointHint")} spellcheck={false} />
            <Input id="offsite-bucket" label={$t("offsite.form.bucket")} bind:value={bucket} spellcheck={false} />
            <Input id="offsite-prefix" label={$t("offsite.form.prefix")} bind:value={prefix} spellcheck={false} />
            <Input id="offsite-region" label={$t("offsite.form.region")} bind:value={region} spellcheck={false} />
            <Input id="offsite-access-key" label={$t("offsite.form.accessKeyId")} bind:value={accessKeyId} autocomplete="off" spellcheck={false} />
            <Input
              id="offsite-secret-key"
              type="password"
              label={$t("offsite.form.secretKey")}
              bind:value={secretKey}
              autocomplete="new-password"
              hint={keepsSecret ? $t("offsite.form.secretKept") : ""}
            />
            <Input id="offsite-limit-upload" type="number" label={$t("offsite.form.limitUpload")} bind:value={limitUpload} hint={$t("offsite.form.limitHint")} />
            <Input id="offsite-limit-download" type="number" label={$t("offsite.form.limitDownload")} bind:value={limitDownload} hint={$t("offsite.form.limitHint")} />
          </div>
        {/if}

        <fieldset class="space-y-2 text-sm">
          <legend class="form-label">{$t("offsite.form.sources")}</legend>
          {#each answer.pools as pool}
            <label class="flex items-center gap-2 break-all">
              <input type="checkbox" value={pool.path} bind:group={poolSources} />
              <span class="font-mono text-xs">{pool.path}</span>
            </label>
          {/each}
          {#each extraSources as source}
            <div class="flex items-center gap-2">
              <span class="font-mono text-xs break-all flex-1 min-w-0">{source}</span>
              <Button size="icon-sm" variant="ghost" icon={Trash2} on:click={() => removeSource(source)}>
                <span class="sr-only">{$t("offsite.form.removeSource", { path: source })}</span>
              </Button>
            </div>
          {/each}
          <div class="flex flex-wrap items-end gap-2">
            <div class="min-w-0 flex-1 basis-56">
              <Input id="offsite-new-source" label={$t("offsite.form.newSource")} bind:value={newSource} spellcheck={false} />
            </div>
            <Button variant="secondary" icon={Plus} disabled={!newSource.trim()} on:click={addSource}>{$t("offsite.form.addSource")}</Button>
          </div>
          <p class="form-hint">{sources.length ? $t("offsite.form.sourcesHint") : $t("offsite.form.noSources")}</p>
        </fieldset>

        <Alert variant="warning">{$t("offsite.form.passwordWarning")}</Alert>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Input
            id="offsite-password"
            type="password"
            label={$t("offsite.form.password")}
            bind:value={password}
            autocomplete="new-password"
            error={passwordTooShort ? $t("offsite.form.passwordTooShort", { n: MIN_PASSWORD_LENGTH }) : ""}
            hint={keepsPassword ? $t("offsite.form.passwordKept") : $t("offsite.form.passwordNeeded", { n: MIN_PASSWORD_LENGTH })}
          />
          <Input
            id="offsite-password-again"
            type="password"
            label={$t("offsite.form.passwordAgain")}
            bind:value={passwordAgain}
            autocomplete="new-password"
            error={passwordAgain && passwordsDiffer ? $t("offsite.form.passwordsDiffer") : ""}
          />
        </div>

        <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Input id="offsite-keep-daily" type="number" label={$t("offsite.form.keepDaily")} bind:value={keepDaily} />
          <Input id="offsite-keep-weekly" type="number" label={$t("offsite.form.keepWeekly")} bind:value={keepWeekly} />
          <Input id="offsite-keep-monthly" type="number" label={$t("offsite.form.keepMonthly")} bind:value={keepMonthly} />
          <Input id="offsite-check-percent" type="number" label={$t("offsite.form.checkPercent")} bind:value={checkPercent} />
        </div>

        <div class="flex flex-wrap gap-2">
          <Button type="submit" loading={saving} disabled={!canSave}>
            {saving ? $t("offsite.form.saving") : $t("offsite.form.save")}
          </Button>
          {#if answer.configured}
            <Button variant="danger" icon={Trash2} disabled={running !== null} on:click={forget}>{$t("offsite.form.forget")}</Button>
          {/if}
        </div>
      </form>
    {/if}
  </Card>
</section>
