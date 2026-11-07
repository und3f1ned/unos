<script lang="ts">
  import { onMount } from "svelte";
  import { ArrowUpCircle, ExternalLink, Package, RefreshCw, Terminal } from "lucide-svelte";
  import { checkForUpdates, getCurrentVersion, type CurrentVersion, type UpdateCheck } from "$lib/api/updates";
  import { Alert, Badge, Button, Card } from "$lib/components/ui";

  // What works until the in-app updater does (ADR-0233, ADR-0173): the
  // version the supervisor image was built with, the latest release, and the
  // update script on the host. The tab has no Install Update or Rollback.

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

  function formatDate(date: string): string {
    return new Date(date).toLocaleDateString();
  }

  $: release = check?.release_info ?? null;
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">Software Updates</h2>

  <Card>
    <h3 class="section-title mb-4">
      <Package class="w-5 h-5" />
      Current version
    </h3>
    {#if currentError}
      <Alert variant="error" title="The current version could not be read">{currentError}</Alert>
    {:else if current}
      <p class="text-sm flex flex-wrap items-center gap-2">
        <span class="text-secondary">Version:</span>
        <span class="mono">{current.version}</span>
        {#if current.is_development}
          <Badge variant="warning">dev</Badge>
        {/if}
      </p>
    {:else}
      <p class="text-sm text-muted">Loading...</p>
    {/if}
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <ArrowUpCircle class="w-5 h-5" />
      Latest release
    </h3>
    <div class="space-y-4">
      {#if checking && !check && !checkError}
        <p class="text-sm text-muted">Checking GitHub for releases...</p>
      {:else if checkError}
        <div role="alert">
          <Alert variant="error" title="Could not check for updates">{checkError}</Alert>
        </div>
      {:else if check && !release}
        <p class="text-sm">No release is published yet.</p>
      {:else if check && release}
        {#if check.update_available}
          <Alert variant="info" title="Update available: {release.tag_name}">
            <p>You run {check.current_version}. Update with the script below.</p>
          </Alert>
        {:else}
          <p class="text-sm">You run the latest release, {release.tag_name}.</p>
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
              View on GitHub <ExternalLink class="w-3 h-3" />
            </a>
          </div>
          <p class="text-xs text-muted flex flex-wrap items-center gap-2">
            Published {formatDate(release.published_at)}
            {#if release.prerelease}
              <Badge variant="warning">Pre-release</Badge>
            {/if}
          </p>
          {#if release.body}
            <p class="text-sm text-secondary whitespace-pre-wrap max-h-48 overflow-y-auto scrollbar-thin">{release.body}</p>
          {/if}
        </div>
      {/if}
      <div class="flex flex-wrap items-center gap-4">
        <Button variant="secondary" size="sm" icon={RefreshCw} loading={checking} on:click={runCheck}>
          Check again
        </Button>
        <label class="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            bind:checked={includePrereleases}
            on:change={runCheck}
            disabled={checking}
            class="w-4 h-4 rounded"
          />
          Include pre-releases
        </label>
      </div>
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <Terminal class="w-5 h-5" />
      How to update
    </h3>
    <div class="space-y-3 text-sm">
      <p>UNOS is updated on the host, with its update script. Run it there as root:</p>
      <p class="card-nested p-3"><code class="mono">{UPDATE_COMMAND}</code></p>
      <p class="text-secondary">
        It backs up the database and the data volume, builds the new version while UNOS keeps running,
        and starts the previous version again if the new one does not answer. The web interface cannot
        install or roll back updates yet.
      </p>
    </div>
  </Card>
</div>
