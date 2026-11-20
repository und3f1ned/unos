<script lang="ts">
  import { messageOf } from "$lib/errors";
  // The off-device copy (ADR-0337), a section of System > Backup & Restore: the copy target with its
  // password (a disk, an S3 store, a REST server or an SFTP server with UNOS's key pair and the host
  // key pinned at its first test, ADR-0422), the folders to copy, the state of the last copy and restore with Run now, a daily task
  // of the Scheduler, and the snapshots with their restores. The state is read when the section
  // opens and on Refresh: copies and restores run in the background, and nothing polls (ADR-0006).
  import { onMount, tick } from "svelte";
  import { ApiError } from "$lib/api/client";
  import {
    getOffsiteSettings,
    getOffsiteSnapshots,
    getOffsiteStatus,
    makeOffsiteKeyPair,
    removeOffsiteSettings,
    restoreOffsiteConfiguration,
    restoreOffsiteFiles,
    runOffsiteCopy,
    saveOffsiteSettings,
    type OffsiteSettingsAnswer,
    type OffsiteSnapshot,
    type OffsiteStatus,
  } from "$lib/api/offsite";
  import { getTasks, type ScheduledTask } from "$lib/api/scheduler";
  import { Alert, Card, Skeleton, confirm } from "$lib/components/ui";
  import { OffsiteForm } from "$lib/components/system/offsite/form.svelte";
  import { restorePoolsOf } from "$lib/components/system/offsite/offsite";
  import OffsiteSettingsForm from "$lib/components/system/offsite/OffsiteSettingsForm.svelte";
  import OffsiteSnapshots from "$lib/components/system/offsite/OffsiteSnapshots.svelte";
  import OffsiteState from "$lib/components/system/offsite/OffsiteState.svelte";
  import { t } from "$lib/i18n";

  type Props = {
    // The units of sizes in the language of the page (formatBytes).
    sizeUnits: string[];
    /** A configuration restored from a snapshot, with the file name it has in the backup list. */
    onrestored?: (filename: string) => void;
  };

  let { sizeUnits, onrestored }: Props = $props();

  // What the server names as words, with the keys of their texts (ADR-0287).
  const WARNINGS: Record<string, string> = { same_disk: "offsite.warning.sameDisk" };

  let loading = $state(true);
  let answer = $state<OffsiteSettingsAnswer | null>(null);
  let status = $state<OffsiteStatus | null>(null);
  let snapshots = $state<OffsiteSnapshot[]>([]);
  let snapshotsError = $state("");
  let tasks = $state<ScheduledTask[]>([]);
  // The last failed action, until it is dismissed; and what the last action did.
  let error = $state<{ title: string; message: string } | null>(null);
  let errorBox = $state<HTMLDivElement>();
  let notice = $state("");

  // The form, from the stored settings of the last answer.
  const form = new OffsiteForm(() => answer);
  let makingKey = $state(false);
  let saving = $state(false);
  let starting = $state(false);
  let restoring = $state<string | null>(null);
  // The snapshot whose Restore files form is open, with its pool and folder.
  let filesOf = $state<string | null>(null);
  let restorePool = $state("");
  let restorePath = $state("");

  let canSave = $derived(!saving && form.complete);
  let restorePools = $derived(restorePoolsOf(answer, form.sources));
  let running = $derived(status?.running ?? null);

  function warningText(warning: { kind: string; message: string }): string {
    return Object.prototype.hasOwnProperty.call(WARNINGS, warning.kind) ? $t(WARNINGS[warning.kind]) : warning.message;
  }

  function sessionEnded(e: unknown): boolean {
    return e instanceof ApiError && e.status === 401;
  }

  async function showError(title: string, e: unknown) {
    if (sessionEnded(e)) return;
    error = { title, message: messageOf(e) };
    await tick();
    errorBox?.scrollIntoView({ block: "nearest" });
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
      if (!sessionEnded(e)) snapshotsError = messageOf(e);
    }
  }

  async function load() {
    loading = true;
    try {
      const [found, state] = await Promise.all([getOffsiteSettings(), getOffsiteStatus()]);
      answer = found;
      status = state;
      form.fill(found);
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

  async function save() {
    saving = true;
    error = null;
    notice = "";
    const request = form.request();
    try {
      answer = await saveOffsiteSettings(request);
      form.fill(answer);
      notice = $t("offsite.form.saved");
      await refresh();
    } catch (e) {
      showError($t("offsite.form.saveFailed"), e);
    }
    saving = false;
  }

  async function makeKeyPair() {
    if (answer?.sftp_public_key) {
      const yes = await confirm({
        title: $t("offsite.sftp.newKeyTitle"),
        message: $t("offsite.sftp.confirmNewKey"),
        confirmLabel: $t("offsite.sftp.newKeyAction"),
        tone: "warning",
      });
      if (!yes) return;
    }
    makingKey = true;
    error = null;
    notice = "";
    try {
      const found = await makeOffsiteKeyPair();
      if (answer) answer = { ...answer, sftp_public_key: found.sftp_public_key };
      notice = $t("offsite.sftp.keyMade");
    } catch (e) {
      showError($t("offsite.sftp.keyFailed"), e);
    }
    makingKey = false;
  }

  async function forget() {
    const yes = await confirm({
      title: $t("offsite.form.forgetTitle"),
      message: $t("offsite.form.confirmForget"),
      confirmLabel: $t("offsite.form.forgetAction"),
    });
    if (!yes) return;
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

  async function restoreConfiguration(snapshot: OffsiteSnapshot) {
    restoring = snapshot.id;
    error = null;
    notice = "";
    try {
      const result = await restoreOffsiteConfiguration(snapshot.id);
      notice = $t("offsite.snapshots.inBackupList", { name: result.backup.filename });
      onrestored?.(result.backup.filename);
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

<!-- Status first, settings apart: the state and the snapshots in one card, the target
     and what is copied in a second one under it. -->
<section aria-labelledby="offsite-title" class="space-y-4">
  <Card>
    <div class="card-header">
      <div class="min-w-0">
        <h3 id="offsite-title" class="card-title">{$t("offsite.title")}</h3>
        <p class="card-description">{$t("offsite.intro")}</p>
      </div>
    </div>

    {#if error}
      <!-- role="alert": screen readers announce the message when it appears. -->
      <div bind:this={errorBox} role="alert" class="mb-4">
        <Alert variant="error" title={error.title} dismissible ondismiss={() => (error = null)}>
          {error.message}
        </Alert>
      </div>
    {/if}
    {#if notice}
      <div role="status" class="mb-4">
        <Alert variant="success" dismissible ondismiss={() => (notice = "")}>{notice}</Alert>
      </div>
    {/if}

    {#if loading && !answer}
      <!-- The first load (ADR-0430); a reload keeps what is shown. -->
      <div aria-busy="true"><Skeleton count={3} /></div>
    {:else if answer}
      {#if !answer.configured}
        <p class="text-sm mb-4">{$t("offsite.notSetUp")}</p>
      {:else}
        {#each answer.warnings as warning}
          <Alert variant="warning" class="mb-4">{warningText(warning)}</Alert>
        {/each}

        <!-- The state: what runs, the last copy and the last restore. -->
        <OffsiteState
          {status}
          {tasks}
          {sizeUnits}
          {starting}
          onrefresh={refresh}
          onrunnow={runNow}
        />

        <!-- The snapshots, with their restores. -->
        <OffsiteSnapshots
          {snapshots}
          {snapshotsError}
          {sizeUnits}
          {running}
          {restoring}
          {filesOf}
          {restorePools}
          bind:restorePool
          bind:restorePath
          onrestoreconfiguration={restoreConfiguration}
          onopenfiles={openFilesForm}
          onrestorefiles={restoreFiles}
          oncancelfiles={() => (filesOf = null)}
        />
      {/if}
    {/if}
  </Card>

  {#if answer}
    <!-- The target and what is copied: saving tests the target. -->
    <Card title={$t("offsite.settings.title")}>
      <OffsiteSettingsForm
        {form}
        {answer}
        {running}
        {saving}
        {canSave}
        {makingKey}
        onsave={save}
        onmakekey={makeKeyPair}
        onforget={forget}
      />
    </Card>
  {:else if loading}
    <!-- The settings' card while the first load runs (ADR-0430), where it will be. -->
    <div aria-busy="true"><Skeleton shape="card" count={4} /></div>
  {/if}
</section>
