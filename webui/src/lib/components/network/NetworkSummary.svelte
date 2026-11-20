<script lang="ts">
  // The cards at the top of System > Network: the default gateway, the first name server and the
  // number of sockets.
  import type { DNSConfig, GatewayInfo } from "$lib/api/network";
  import type { IconComponent } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { Globe, Link2, Server } from "lucide-svelte";

  type Props = {
    gateway: GatewayInfo | null;
    dnsConfig: DNSConfig | null;
    connectionCount: number;
  };

  let { gateway, dnsConfig, connectionCount }: Props = $props();
</script>

{#snippet heading(Icon: IconComponent, title: string)}
  <div class="flex items-center gap-2 text-content-secondary text-sm mb-2">
    <Icon class="w-4 h-4" />
    {title}
  </div>
{/snippet}

<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
  <div class="card">
    {@render heading(Globe, $t("network.summary.gateway"))}
    <p class="text-lg font-semibold text-content break-all">{gateway?.gateway || $t("network.summary.notSet")}</p>
    {#if gateway?.interface}
      <p class="text-xs text-content-muted">{$t("network.summary.via", { name: gateway.interface })}</p>
    {/if}
  </div>

  <div class="card">
    {@render heading(Server, $t("network.summary.dns"))}
    {#if dnsConfig?.nameservers?.length}
      <p class="text-lg font-semibold text-content break-all">{dnsConfig.nameservers[0]}</p>
      {#if dnsConfig.nameservers.length > 1}
        <p class="text-xs text-content-muted">{$t("network.more", { n: dnsConfig.nameservers.length - 1 })}</p>
      {/if}
    {:else}
      <p class="text-content-muted">{$t("network.summary.notConfigured")}</p>
    {/if}
  </div>

  <div class="card">
    {@render heading(Link2, $t("network.summary.connections"))}
    <p class="text-lg font-semibold text-content">{connectionCount}</p>
    <p class="text-xs text-content-muted">{$t("network.summary.sockets")}</p>
  </div>
</div>
