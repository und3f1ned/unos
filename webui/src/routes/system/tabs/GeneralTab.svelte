<script lang="ts">
  import { onMount } from "svelte";
  import { getSystemInfo, getTimezone, getTimezones, setTimezone, type SystemInfo } from "$lib/api/system";
  import { getHostname, setHostname } from "$lib/api/network";
  import { getCurrentVersion, type CurrentVersion } from "$lib/api/updates";
  import { user } from "$lib/stores/auth";
  import { Alert, Badge, Button, Card, Input, Select } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { apiTime } from "$lib/time";
  import { Server, User, Package, Globe } from "lucide-svelte";

  let systemInfo = $state<SystemInfo | null>(null);
  let currentVersion = $state<CurrentVersion | null>(null);
  let loading = $state(true);

  // The name and time zone of the host (ADR-0331): each read and saved on its own.
  let hostname = $state("");
  let savedHostname = $state("");
  let zone = $state("");
  let savedZone = $state("");
  let zones = $state<string[]>([]);
  let hostLoading = $state(true);
  let hostLoadFailed = $state(false);
  let savingHostname = $state(false);
  let savingZone = $state(false);
  let hostNotice = $state("");
  let hostError = $state("");

  let hostnameLoaded = $state(false);
  let zoneLoaded = $state(false);

  // The host's zone first when the host does not list it, so that the page shows it.
  let zoneOptions = $derived([
    ...(savedZone && savedZone !== "Unknown" && !zones.includes(savedZone) ? [savedZone] : []),
    ...zones,
  ].map((name) => ({ value: name, label: name })));

  async function loadHost() {
    // Each row from its own answers: one that fails leaves the other usable.
    const [name, current, listed] = await Promise.allSettled([getHostname(), getTimezone(), getTimezones()]);
    if (name.status === "fulfilled") {
      hostname = savedHostname = name.value.hostname;
      hostnameLoaded = true;
    }
    if (current.status === "fulfilled") {
      zone = savedZone = current.value.timezone;
      zoneLoaded = true;
    }
    if (listed.status === "fulfilled") zones = listed.value.timezones;
    hostLoadFailed = !hostnameLoaded && !zoneLoaded;
    hostLoading = false;
  }

  async function saveHostname() {
    const name = hostname.trim();
    savingHostname = true;
    hostNotice = hostError = "";
    try {
      const answer = await setHostname(name);
      hostname = savedHostname = answer.hostname;
      if (systemInfo) systemInfo = { ...systemInfo, hostname: answer.hostname };
      hostNotice = $t("general.host.hostnameSaved", { name: answer.hostname });
    } catch (e) {
      hostError = e instanceof Error ? e.message : $t("general.host.saveFailed");
    } finally {
      savingHostname = false;
    }
  }

  async function saveZone() {
    savingZone = true;
    hostNotice = hostError = "";
    try {
      const answer = await setTimezone(zone);
      zone = savedZone = answer.timezone;
      hostNotice = $t("general.host.zoneSaved", { zone: answer.timezone });
    } catch (e) {
      hostError = e instanceof Error ? e.message : $t("general.host.saveFailed");
      zone = savedZone; // the host kept its zone
    } finally {
      savingZone = false;
    }
  }

  onMount(async () => {
    loadHost();
    try {
      const [info, version] = await Promise.all([
        getSystemInfo(),
        getCurrentVersion(),
      ]);
      systemInfo = info;
      currentVersion = version;
    } catch (e) {
      console.error("Failed to load system info:", e);
    } finally {
      loading = false;
    }
  });

  // Reactive, so that the uptime follows a change of the language.
  let formatUptime = $derived((bootTime: string): string => {
    const booted = apiTime(bootTime);
    if (!booted) return bootTime;
    const seconds = Math.floor((Date.now() - booted.getTime()) / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days > 0) return $t("general.uptime.days", { days, hours, minutes });
    if (hours > 0) return $t("general.uptime.hours", { hours, minutes });
    return $t("general.uptime.minutes", { minutes });
  });

  // A version in the language of the page: the server answers "unknown" when UNOS was built
  // without one (get_current_version of supervisor/src/updates/updater.py).
  let versionLabel = $derived((version: string): string => (version === "unknown" ? $t("general.version.unknown") : version));
</script>

<div class="space-y-6">
  <h2 class="text-xl font-semibold">{$t("general.title")}</h2>

  <!-- System Info -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <Server class="w-5 h-5" />
      {$t("general.info.title")}
    </h3>

    {#if loading}
      <p class="text-content-muted">{$t("common.loading")}</p>
    {:else if systemInfo}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
        <div>
          <span class="text-content-secondary">{$t("general.info.hostname")}</span>
          <span class="ml-2 font-medium break-words">{systemInfo.hostname}</span>
        </div>
        <div>
          <span class="text-content-secondary">{$t("general.info.os")}</span>
          <span class="ml-2">{systemInfo.os}</span>
        </div>
        <div>
          <span class="text-content-secondary">{$t("general.info.kernel")}</span>
          <span class="ml-2 font-mono text-xs">{systemInfo.kernel}</span>
        </div>
        <div>
          <span class="text-content-secondary">{$t("general.info.architecture")}</span>
          <span class="ml-2">{systemInfo.architecture}</span>
        </div>
        <div>
          <span class="text-content-secondary">{$t("general.info.uptime")}</span>
          <span class="ml-2">{formatUptime(systemInfo.boot_time)}</span>
        </div>
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.info.loadFailed")}</p>
    {/if}
  </div>

  <!-- The name and time zone of the host, set on the host (ADR-0331) -->
  <Card>
    <h3 class="section-title mb-4">
      <Globe class="w-5 h-5" />
      {$t("general.host.title")}
    </h3>

    {#if hostLoading}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else if hostLoadFailed}
      <p class="text-sm text-muted">{$t("general.host.loadFailed")}</p>
    {:else}
      <div class="space-y-4">
        {#if hostnameLoaded}
          <div>
            <form class="flex flex-wrap items-end gap-3" onsubmit={(e) => { e.preventDefault(); saveHostname(); }}>
              <div class="min-w-0 flex-1 basis-56">
                <Input id="general-hostname" label={$t("general.host.hostname")} bind:value={hostname} maxlength={63} />
              </div>
              <Button
                type="submit"
                variant="secondary"
                loading={savingHostname}
                disabled={!hostname.trim() || hostname.trim() === savedHostname}
              >
                {$t("general.host.saveHostname")}
              </Button>
            </form>
            <p class="form-hint">{$t("general.host.hostnameHint")}</p>
          </div>
        {/if}
        {#if zoneLoaded}
          <div>
            <div class="flex flex-wrap items-end gap-3">
              <div class="min-w-0 flex-1 basis-56">
                <Select
                  id="general-zone"
                  label={$t("general.host.zone")}
                  bind:value={zone}
                  options={zoneOptions}
                  placeholder={$t("general.host.unknownZone")}
                  disabled={zones.length === 0}
                />
              </div>
              <Button variant="secondary" loading={savingZone} disabled={!zone || zone === savedZone} onclick={saveZone}>
                {$t("general.host.saveZone")}
              </Button>
            </div>
            <p class="form-hint">{zones.length === 0 ? $t("general.host.noZones") : $t("general.host.zoneHint")}</p>
          </div>
        {/if}
        <div role="status">
          {#if hostNotice}
            <Alert variant="success" dismissible ondismiss={() => (hostNotice = "")}>{hostNotice}</Alert>
          {/if}
        </div>
        <div role="alert">
          {#if hostError}
            <Alert variant="error" dismissible ondismiss={() => (hostError = "")}>{hostError}</Alert>
          {/if}
        </div>
      </div>
    {/if}
  </Card>

  <!-- UNOS Version: the version the supervisor image was built with (ADR-0234) -->
  <Card>
    <h3 class="section-title mb-4">
      <Package class="w-5 h-5" />
      {$t("general.version.title")}
    </h3>

    {#if loading}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else if currentVersion}
      <p class="text-sm flex flex-wrap items-center gap-2">
        <span class="text-secondary">{$t("general.version.label")}</span>
        <span class="mono">{versionLabel(currentVersion.version)}</span>
        {#if currentVersion.is_development}
          <Badge variant="warning">{$t("general.version.dev")}</Badge>
        {/if}
      </p>
      <Button href="#updates" variant="secondary" size="sm" class="mt-3">{$t("general.version.check")}</Button>
    {:else}
      <p class="text-sm text-muted">{$t("general.version.unavailable")}</p>
    {/if}
  </Card>

  <!-- User Account -->
  <div class="bg-surface-elevated rounded-lg p-4">
    <h3 class="text-lg font-semibold mb-4 flex items-center gap-2">
      <User class="w-5 h-5" />
      {$t("general.user.title")}
    </h3>

    {#if $user}
      <div class="space-y-3">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div>
            <span class="text-content-secondary">{$t("general.user.username")}</span>
            <span class="ml-2 font-medium">{$user.username}</span>
          </div>
          <div>
            <span class="text-content-secondary">{$t("general.user.role")}</span>
            <span class="ml-2">
              {#if $user.is_admin}
                <span class="px-2 py-0.5 bg-status-info/20 text-status-info rounded text-xs">{$t("general.user.admin")}</span>
              {:else}
                <span class="px-2 py-0.5 bg-status-neutral/20 text-content-secondary rounded text-xs">{$t("general.user.user")}</span>
              {/if}
            </span>
          </div>
        </div>
        <a
          href="/users"
          class="inline-block px-4 py-2 bg-surface-overlay hover:bg-surface-hover rounded-lg transition-colors text-sm"
        >
          {$t("general.user.manage")}
        </a>
      </div>
    {:else}
      <p class="text-content-muted">{$t("general.user.signedOut")}</p>
    {/if}
  </div>
</div>
