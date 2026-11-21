<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount } from "svelte";
  import { ArrowRight, ExternalLink, RefreshCw } from "lucide-svelte";
  import { checkForUpdates, getCurrentVersion, type CurrentVersion, type UpdateCheck } from "$lib/api/updates";
  import { Alert, Badge, Button, Card, Skeleton, Switch, HelpText } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDate } from "$lib/time";

  // What works until the in-app updater does (ADR-0234, ADR-0173): the
  // version the supervisor image was built with, the latest release, and the
  // update script on the host. The tab has no Install Update or Rollback. It is
  // the one place of the version (ADR-0431).

  // i18n-ignore: a command, the same in every language
  const UPDATE_COMMAND = "sudo /opt/unos/deploy/update.sh";

  let current = $state<CurrentVersion | null>(null);
  let currentError = $state("");
  let check = $state<UpdateCheck | null>(null);
  let checkError = $state("");
  let checking = $state(false);
  let includePrereleases = $state(false);

  onMount(() => {
    void loadCurrent();
    void runCheck();
  });

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
  let formatShownDate = $derived((date: string): string => formatDate(date, $intlLocale));

  let release = $derived(check?.release_info ?? null);

  // A version in the language of the page: the server answers "unknown" when UNOS was built
  // without one (get_current_version of supervisor/src/updates/updater.py).
  let versionLabel = $derived((version: string): string => (version === "unknown" ? $t("updates.current.unknown") : version));
</script>

<!-- One card: the version that runs, the newest release and how to update to it, in
     place of a card for the current version alone, one for the release and one of instructions. -->
<div class="space-y-6">
  <h2 class="text-xl font-semibold">{$t("updates.title")}</h2>

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
            <p class="text-sm text-content-secondary">{$t("updates.latest.howTo")}</p>
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

      <section class="border-t border-border pt-4 space-y-2 text-sm" aria-labelledby="updates-how-title">
        <h4 id="updates-how-title" class="font-semibold text-content">{$t("updates.how.title")}</h4>
        <p class="text-content-secondary">{$t("updates.how.run")}</p>
        <p class="card-nested py-2"><code class="mono select-all break-all">{UPDATE_COMMAND}</code></p>
        <HelpText text="" moreLabel={$t("updates.how.more")}>
          {#snippet more()}<p>{$t("updates.how.details")}</p>{/snippet}
        </HelpText>
      </section>
    </div>
  </Card>
</div>
