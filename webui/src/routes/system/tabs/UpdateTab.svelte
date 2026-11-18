<script lang="ts">
  import { onMount } from "svelte";
  import { ArrowUpCircle, ExternalLink, Package, RefreshCw, Terminal } from "lucide-svelte";
  import { checkForUpdates, getCurrentVersion, type CurrentVersion, type UpdateCheck } from "$lib/api/updates";
  import { Alert, Badge, Button, Card } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDate } from "$lib/time";

  // What works until the in-app updater does (ADR-0234, ADR-0173): the
  // version the supervisor image was built with, the latest release, and the
  // update script on the host. The tab has no Install Update or Rollback.

  // i18n-ignore: a command, the same in every language
  const UPDATE_COMMAND = "sudo /opt/unos/deploy/update.sh";

  let current: CurrentVersion | null = null;
  let currentError = "";
  let check: UpdateCheck | null = null;
  let checkError = "";
  let checking = false;
  let includePrereleases = false;

  onMount(() => {
    void loadCurrent();
    void runCheck();
  });

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadCurrent() {
    try {
      current = await getCurrentVersion();
      currentError = "";
    } catch (e) {
      currentError = messageOf(e);
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

  // Reactive, so that the date follows a change of the language.
  $: formatShownDate = (date: string): string => formatDate(date, $intlLocale);

  $: release = check?.release_info ?? null;

  // A version in the language of the page: the server answers "unknown" when UNOS was built
  // without one (get_current_version of supervisor/src/updates/updater.py).
  $: versionLabel = (version: string): string => (version === "unknown" ? $t("updates.current.unknown") : version);
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">{$t("updates.title")}</h2>

  <Card>
    <h3 class="section-title mb-4">
      <Package class="w-5 h-5" />
      {$t("updates.current.title")}
    </h3>
    {#if currentError}
      <Alert variant="error" title={$t("updates.current.error")}>{currentError}</Alert>
    {:else if current}
      <p class="text-sm flex flex-wrap items-center gap-2">
        <span class="text-secondary">{$t("updates.current.version")}</span>
        <span class="mono">{versionLabel(current.version)}</span>
        {#if current.is_development}
          <Badge variant="warning">{$t("updates.current.dev")}</Badge>
        {/if}
      </p>
    {:else}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {/if}
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <ArrowUpCircle class="w-5 h-5" />
      {$t("updates.latest.title")}
    </h3>
    <div class="space-y-4">
      {#if checking && !check && !checkError}
        <p class="text-sm text-muted">{$t("updates.latest.checking")}</p>
      {:else if checkError}
        <div role="alert">
          <Alert variant="error" title={$t("updates.latest.checkFailed")}>{checkError}</Alert>
        </div>
      {:else if check && !release}
        <p class="text-sm">{$t("updates.latest.none")}</p>
      {:else if check && release}
        {#if check.update_available}
          <Alert variant="info" title={$t("updates.latest.available", { tag: release.tag_name })}>
            <p>
              {check.current_version === "unknown"
                ? $t("updates.latest.youRunUnknown")
                : $t("updates.latest.youRun", { version: check.current_version })}
            </p>
          </Alert>
        {:else}
          <p class="text-sm">{$t("updates.latest.upToDate", { tag: release.tag_name })}</p>
        {/if}
        <div class="card-nested p-4 space-y-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h4 class="card-title">{release.name || release.tag_name}</h4>
            <a
              href={release.html_url}
              target="_blank"
              rel="noopener noreferrer"
              class="flex items-center gap-1 text-sm text-status-info hover:underline"
            >
              {$t("updates.latest.github")} <ExternalLink class="w-3 h-3" />
            </a>
          </div>
          <p class="text-xs text-muted flex flex-wrap items-center gap-2">
            {$t("updates.latest.published", { date: formatShownDate(release.published_at) })}
            {#if release.prerelease}
              <Badge variant="warning">{$t("updates.latest.prerelease")}</Badge>
            {/if}
          </p>
          {#if release.body}
            <p class="text-sm text-secondary whitespace-pre-wrap max-h-48 overflow-y-auto scrollbar-thin">{release.body}</p>
          {/if}
        </div>
      {/if}
      <div class="flex flex-wrap items-center gap-4">
        <Button variant="secondary" size="sm" icon={RefreshCw} loading={checking} on:click={runCheck}>
          {$t("updates.latest.checkAgain")}
        </Button>
        <label class="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            bind:checked={includePrereleases}
            on:change={runCheck}
            disabled={checking}
            class="w-4 h-4 rounded"
          />
          {$t("updates.latest.includePrereleases")}
        </label>
      </div>
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <Terminal class="w-5 h-5" />
      {$t("updates.how.title")}
    </h3>
    <div class="space-y-3 text-sm">
      <p>{$t("updates.how.run")}</p>
      <p class="card-nested p-3"><code class="mono">{UPDATE_COMMAND}</code></p>
      <p class="text-secondary">{$t("updates.how.details")}</p>
    </div>
  </Card>
</div>
