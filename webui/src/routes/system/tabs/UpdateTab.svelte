<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount, untrack } from "svelte";
  import { ArrowRight, Download, ExternalLink, RefreshCw } from "lucide-svelte";
  import {
    checkForUpdates,
    getCurrentVersion,
    getUpdateConfig,
    getUpdateHistory,
    getUpdateProgress,
    installUpdate,
    rollbackUpdate,
    saveUpdateConfig,
    updateRuns,
    type CurrentVersion,
    type UpdateCheck,
    type UpdateConfig,
    type UpdateHistoryEntry,
    type UpdateProgress,
  } from "$lib/api/updates";
  import { Alert, Badge, Button, Card, Skeleton, Switch, HelpText, TechDetails, confirm, toast } from "$lib/components/ui";
  import { AutoInstallCard, UpdateHistoryCard, UpdateJobCard } from "$lib/components/system/updates";
  import { intlLocale, t } from "$lib/i18n";
  import { nasTimeZone } from "$lib/stores/nasTime";
  import { updateProgress } from "$lib/stores/updates";
  import { formatDate, knownZone } from "$lib/time";

  // The version the supervisor image was built with, the latest release, and how to update to it
  // (ADR-0234, ADR-0439). Install Update, Rollback, the last update, the history and the automatic
  // install show only where the supervisor can install releases (install_unavailable of its
  // progress is null); elsewhere the tab names the update script on the host, as before. It is the
  // one place of the version (ADR-0431).

  // i18n-ignore: a command, the same in every language
  const UPDATE_COMMAND = "sudo /opt/unos/deploy/update.sh";

  let current = $state<CurrentVersion | null>(null);
  let currentError = $state("");
  let check = $state<UpdateCheck | null>(null);
  let checkError = $state("");
  let checking = $state(false);
  let includePrereleases = $state(false);
  // What GET /updates/progress answered at the load; the events socket's steps replace it.
  let loadedProgress = $state.raw<UpdateProgress | null>(null);
  // The first answer of GET /updates/progress came (or failed): until then the parts of the updater
  // show a skeleton (ADR-0430).
  let updaterKnown = $state(false);
  let history = $state.raw<UpdateHistoryEntry[]>([]);
  let config = $state.raw<UpdateConfig | null>(null);
  let starting = $state(false);
  let rollingBack = $state(false);
  let savingConfig = $state(false);

  let progress = $derived($updateProgress ?? loadedProgress);
  let installable = $derived(progress !== null && progress.install_unavailable === null);
  let runs = $derived(updateRuns(progress));
  // The NAS's zone when this browser knows it: the history and the next look are on its clock.
  let zone = $derived(knownZone($nasTimeZone) ? $nasTimeZone : null);

  onMount(() => {
    void loadCurrent();
    void runCheck();
    void loadUpdater();
  });

  // A job that ended (a step of the events socket): the version, the history and the progress as
  // the server has them now.
  let followed = "";
  $effect(() => {
    const step = $updateProgress;
    untrack(() => {
      const end = step && !updateRuns(step) ? `${step.status} ${step.completed_at}` : "";
      if (end && end !== followed) {
        followed = end;
        void loadCurrent();
        void loadUpdater();
      }
    });
  });

  async function loadCurrent() {
    try {
      current = await getCurrentVersion();
      currentError = "";
    } catch (e) {
      currentError = messageOf(e);
    }
  }

  async function loadUpdater() {
    try {
      const answered = await getUpdateProgress();
      loadedProgress = answered;
      if (answered.install_unavailable === null) {
        [history, config] = await Promise.all([getUpdateHistory(), getUpdateConfig()]);
      }
    } catch {
      // Without the progress the tab shows what it showed before the in-app updater: the script.
    } finally {
      updaterKnown = true;
    }
  }

  async function runCheck() {
    checking = true;
    try {
      check = await checkForUpdates(includePrereleases);
      checkError = "";
    } catch (e) {
      check = null;
      checkError = messageOf(e);
    } finally {
      checking = false;
    }
  }

  async function handleInstall() {
    if (!release || !current) return;
    const tag = release.tag_name;
    const ok = await confirm({
      title: $t("updates.installTitle", { tag }),
      consequences: [
        $t("updates.installSteps.images", { tag }),
        $t("updates.installSteps.restart", { tag }),
        $t("updates.installSteps.rollback", { tag, current: versionLabel(current.version) }),
      ],
      confirmLabel: $t("updates.installConfirm"),
      tone: "warning",
    });
    if (!ok) return;
    starting = true;
    try {
      await installUpdate(tag);
      await loadUpdater();
    } catch (e) {
      toast.error(messageOf(e), { title: $t("updates.installFailed") });
    } finally {
      starting = false;
    }
  }

  async function handleRollback() {
    const ok = await confirm({
      title: $t("updates.rollbackTitle"),
      consequences: [$t("updates.rollbackSteps.database"), $t("updates.rollbackSteps.restart")],
      confirmLabel: $t("updates.rollbackConfirm"),
      tone: "warning",
    });
    if (!ok) return;
    rollingBack = true;
    try {
      await rollbackUpdate();
      await loadUpdater();
    } catch (e) {
      toast.error(messageOf(e), { title: $t("updates.rollbackFailed") });
    } finally {
      rollingBack = false;
    }
  }

  async function handleSaveConfig(next: UpdateConfig) {
    savingConfig = true;
    try {
      config = await saveUpdateConfig(next);
      const fresh = await getUpdateProgress();
      loadedProgress = fresh;
      // The next look is in the progress: a step that the events socket sent before would hide it.
      if ($updateProgress) updateProgress.set(fresh);
      toast.success($t("updates.auto.saved"));
    } catch (e) {
      toast.error(messageOf(e), { title: $t("updates.auto.saveFailed") });
    } finally {
      savingConfig = false;
    }
  }

  // Reactive, so that the date follows a change of the language.
  let formatShownDate = $derived((date: string): string => formatDate(date, $intlLocale));

  let release = $derived(check?.release_info ?? null);
  let canInstall = $derived(
    installable && current !== null && !!check?.update_available && !!release && !release.prerelease,
  );

  // A version in the language of the page: the server answers "unknown" when UNOS was built
  // without one (get_current_version of supervisor/src/updates/updater.py).
  let versionLabel = $derived((version: string): string => (version === "unknown" ? $t("updates.current.unknown") : version));
</script>

<div class="space-y-6">
  <!-- Its height is the primary button's, which comes with the updater's answer. -->
  <div class="flex min-h-10 flex-wrap items-center justify-between gap-3">
    <h2 class="text-xl font-semibold">{$t("updates.title")}</h2>
    {#if canInstall && release}
      <Button
        variant="primary"
        icon={Download}
        loading={starting}
        disabled={runs}
        disabledReason={$t("updates.jobRuns")}
        onclick={handleInstall}
      >
        {$t("updates.install", { tag: release.tag_name })}
      </Button>
    {/if}
  </div>
  <!-- One card: the version that runs, the newest release and how to update to it. The parts of the
       updater, which load on their own, follow it (ADR-0430). -->
  <Card title={$t("updates.card.title")}>
    <div class="space-y-5">
      <div class="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
        <span class="text-content-secondary">{$t("updates.current.version")}</span>
        {#if currentError}
          <div role="alert" class="basis-full">
            <Alert variant="error" size="sm" title={$t("updates.current.error")}>{currentError}</Alert>
          </div>
        {:else if current}
          <span class="mono text-content">{versionLabel(current.version)}</span>
          {#if current.is_development}
            <Badge variant="outline">{$t("updates.current.dev")}</Badge>
          {/if}
          {#if check?.update_available && release}
            <ArrowRight class="h-4 w-4 text-content-muted" aria-hidden="true" />
            <span class="mono text-content">{release.tag_name}</span>
          {/if}
        {:else}
          <span aria-busy="true"><Skeleton class="w-40" /></span>
        {/if}
      </div>

      {#if checking && !check && !checkError}
        <div aria-busy="true" class="space-y-2"><Skeleton count={3} /></div>
      {:else if checkError}
        <div role="alert">
          <Alert variant="error" size="sm" title={$t("updates.latest.checkFailed")}>{checkError}</Alert>
        </div>
      {:else if check && !release}
        <p class="text-sm">{$t("updates.latest.none")}</p>
      {:else if check && release}
        {#if check.update_available}
          <div>
            <p class="text-sm font-medium text-content">{$t("updates.latest.available", { tag: release.tag_name })}</p>
            <p class="text-sm text-content-secondary">
              {#if canInstall}
                {$t("updates.latest.installHere")}
              {:else if installable && release.prerelease}
                {$t("updates.latest.stableOnly")}
              {:else if !installable}
                {$t("updates.latest.howTo")}
              {/if}
            </p>
          </div>
        {:else}
          <p class="text-sm">{$t("updates.latest.upToDate", { tag: release.tag_name })}</p>
        {/if}
        <div class="card-nested space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="text-sm font-semibold text-content">{release.name || release.tag_name}</h4>
            <a
              href={release.html_url}
              target="_blank"
              rel="noopener noreferrer"
              class="link flex items-center gap-1 text-sm"
            >
              {$t("updates.latest.github")} <ExternalLink class="w-3 h-3" />
            </a>
          </div>
          <p class="text-xs text-content-muted flex flex-wrap items-center gap-2">
            {$t("updates.latest.published", { date: formatShownDate(release.published_at) })}
            {#if release.prerelease}
              <Badge variant="warning">{$t("updates.latest.prerelease")}</Badge>
            {/if}
          </p>
          {#if release.body}
            <p class="text-sm text-content-secondary whitespace-pre-wrap max-h-48 overflow-y-auto scrollbar-thin">{release.body}</p>
          {/if}
        </div>
      {/if}

      <div class="flex flex-wrap items-center gap-4">
        <Button variant="secondary" size="sm" icon={RefreshCw} loading={checking} onclick={runCheck}>
          {$t("updates.latest.checkAgain")}
        </Button>
        <!-- It checks again at once: a Switch (ADR-0432). -->
        <label class="flex items-center gap-2 text-sm">
          <Switch bind:checked={includePrereleases} onchange={runCheck} disabled={checking} />
          {$t("updates.latest.includePrereleases")}
        </label>
      </div>

      {#if !updaterKnown}
        <div aria-busy="true" class="border-t border-border pt-4"><Skeleton count={2} /></div>
      {:else if !installable}
        <section class="border-t border-border pt-4 space-y-2 text-sm" aria-labelledby="updates-how-title">
          <h4 id="updates-how-title" class="font-semibold text-content">{$t("updates.how.title")}</h4>
          <p class="text-content-secondary">{$t("updates.how.run")}</p>
          <p class="card-nested py-2"><code class="mono select-all break-all">{UPDATE_COMMAND}</code></p>
          <HelpText text="" moreLabel={$t("updates.how.more")}>
            {#snippet more()}<p>{$t("updates.how.details")}</p>{/snippet}
          </HelpText>
          {#if progress?.install_unavailable}
            <TechDetails summary={$t("updates.how.why")} text={progress.install_unavailable} />
          {/if}
        </section>
      {/if}
    </div>
  </Card>

  {#if installable && zone}
    <HelpText text={$t("updates.timeZone", { zone })} />
  {/if}

  {#if installable && progress && progress.action}
    <UpdateJobCard {progress} {rollingBack} onrollback={handleRollback} />
  {/if}

  {#if installable && history.length > 0}
    <UpdateHistoryCard {history} {zone} />
  {/if}

  {#if installable && config}
    <AutoInstallCard
      {config}
      nextCheck={progress?.next_automatic_check ?? null}
      {zone}
      saving={savingConfig}
      onsave={handleSaveConfig}
    />
  {/if}
</div>
