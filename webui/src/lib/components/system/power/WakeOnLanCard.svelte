<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { Network } from "lucide-svelte";
  import { disableWol, enableWol, type WolInterface } from "$lib/api/power";
  import { Alert, Badge, Button, Card, EmptyState } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { WakeOnLan } from "./wol.svelte";

  // The host's network interfaces, each with its Wake-on-LAN and a button that turns it on or off.
  type Props = {
    wol: WakeOnLan;
    // Wake-on-LAN changed: what the page says.
    onnotice: (message: string) => void;
    // The host refused the change: why.
    onfailure: (message: string) => void;
  };

  let { wol, onnotice, onfailure }: Props = $props();

  async function setWol(iface: WolInterface, on: boolean) {
    if (wol.busy) return;
    wol.busy = iface.name;
    try {
      const updated = on ? await enableWol(iface.name) : await disableWol(iface.name);
      wol.interfaces = wol.interfaces.map((i) => (i.name === updated.name ? updated : i));
      onnotice($t(updated.enabled ? "power.wol.turnedOn" : "power.wol.turnedOff", { name: updated.name }));
    } catch (e) {
      onfailure(messageOf(e));
      // Show what the host has now.
      await wol.load();
    } finally {
      wol.busy = null;
    }
  }
</script>

<Card>
  <h3 class="card-title mb-4">
    <Network class="w-5 h-5 text-content-secondary" />
    {$t("power.wol.title")}
  </h3>
  {#if wol.loading}
    <p class="text-sm text-muted">{$t("common.loading")}</p>
  {:else if wol.error}
    <Alert variant="error" title={$t("power.wol.loadError")}>{wol.error}</Alert>
  {:else if wol.interfaces.length === 0}
    <EmptyState icon={Network} title={$t("power.wol.none")} />
  {:else}
    <div class="table-frame">
      <table class="table">
        <thead class="table-header">
          <tr>
            <th>{$t("power.wol.interface")}</th>
            <th>{$t("power.wol.mac")}</th>
            <th>{$t("power.wol.title")}</th>
            <th><span class="sr-only">{$t("power.wol.change")}</span></th>
          </tr>
        </thead>
        <tbody>
          {#each wol.interfaces as iface (iface.name)}
            <tr class="table-row">
              <td class="mono">{iface.name}</td>
              <td class="mono">{iface.mac_address ?? $t("power.wol.unknownMac")}</td>
              <td>
                {#if !iface.supported}
                  <Badge variant="neutral">{$t("power.wol.notSupported")}</Badge>
                {:else if iface.enabled}
                  <Badge variant="success">{$t("power.wol.on")}</Badge>
                {:else}
                  <Badge variant="neutral">{$t("power.wol.off")}</Badge>
                {/if}
              </td>
              <td class="text-right">
                {#if iface.supported}
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={wol.busy === iface.name}
                    disabled={wol.busy !== null && wol.busy !== iface.name}
                    onclick={() => setWol(iface, !iface.enabled)}
                  >
                    {iface.enabled ? $t("power.wol.turnOff") : $t("power.wol.turnOn")}<span class="sr-only"
                      >{$t("power.wol.forInterface", { name: iface.name })}</span
                    >
                  </Button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="text-sm text-muted mt-3">
      {$t("power.wol.hintBefore")}
      <span class="mono">{$t("power.wol.command")}</span>{$t("power.wol.hintAfter")}
    </p>
  {/if}
</Card>
