<script lang="ts">
  import { Globe } from "lucide-svelte";
  import { setHostname } from "$lib/api/network";
  import { setTimezone } from "$lib/api/system";
  import { Alert, Button, Card, Input, Select } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { HostSettings } from "./host.svelte";

  // The host's name and time zone, each saved on its own; what the host answered.
  type Props = {
    host: HostSettings;
  };

  let { host }: Props = $props();

  let savingHostname = $state(false);
  let savingZone = $state(false);
  let hostNotice = $state("");
  let hostError = $state("");

  async function saveHostname() {
    const name = host.hostname.trim();
    savingHostname = true;
    hostNotice = hostError = "";
    try {
      const answer = await setHostname(name);
      host.hostname = host.savedHostname = answer.hostname;
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
      const answer = await setTimezone(host.zone);
      host.zone = host.savedZone = answer.timezone;
      hostNotice = $t("general.host.zoneSaved", { zone: answer.timezone });
    } catch (e) {
      hostError = e instanceof Error ? e.message : $t("general.host.saveFailed");
      host.zone = host.savedZone; // the host kept its zone
    } finally {
      savingZone = false;
    }
  }
</script>

<!-- The name and time zone of the host, set on the host (ADR-0331) -->
<Card>
  <h3 class="card-title mb-4">
    <Globe class="w-5 h-5 text-content-secondary" />
    {$t("general.host.title")}
  </h3>

  {#if host.loading}
    <p class="text-sm text-muted">{$t("common.loading")}</p>
  {:else if host.loadFailed}
    <p class="text-sm text-muted">{$t("general.host.loadFailed")}</p>
  {:else}
    <div class="space-y-4">
      {#if host.hostnameLoaded}
        <div>
          <form class="flex flex-wrap items-end gap-3" onsubmit={(e) => { e.preventDefault(); saveHostname(); }}>
            <div class="min-w-0 flex-1 basis-56">
              <Input id="general-hostname" label={$t("general.host.hostname")} bind:value={host.hostname} maxlength={63} />
            </div>
            <Button
              type="submit"
              variant="secondary"
              loading={savingHostname}
              disabled={!host.hostname.trim() || host.hostname.trim() === host.savedHostname}
            >
              {$t("general.host.saveHostname")}
            </Button>
          </form>
          <p class="form-hint">{$t("general.host.hostnameHint")}</p>
        </div>
      {/if}
      {#if host.zoneLoaded}
        <div>
          <div class="flex flex-wrap items-end gap-3">
            <div class="min-w-0 flex-1 basis-56">
              <Select
                id="general-zone"
                label={$t("general.host.zone")}
                bind:value={host.zone}
                options={host.zoneOptions}
                placeholder={$t("general.host.unknownZone")}
                disabled={host.zones.length === 0}
              />
            </div>
            <Button variant="secondary" loading={savingZone} disabled={!host.zone || host.zone === host.savedZone} onclick={saveZone}>
              {$t("general.host.saveZone")}
            </Button>
          </div>
          <p class="form-hint">{host.zones.length === 0 ? $t("general.host.noZones") : $t("general.host.zoneHint")}</p>
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
