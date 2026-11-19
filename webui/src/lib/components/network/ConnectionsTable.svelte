<script lang="ts">
  // The host's sockets, as ss prints them (ADR-0295), with their own Refresh.
  import type { Connection } from "$lib/api/network";
  import { Badge, Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  type Props = {
    connections: Connection[];
    onrefresh: () => void;
  };

  let { connections, onrefresh }: Props = $props();
</script>

<div class="card p-0 overflow-hidden">
  <div class="p-3 bg-surface-overlay flex justify-between items-center">
    <span class="text-sm text-content-secondary">{$t("network.connections.count", { n: connections.length })}</span>
    <Button variant="ghost" size="sm" onclick={onrefresh}>{$t("network.connections.refresh")}</Button>
  </div>
  <div class="table-frame max-h-96 overflow-y-auto px-3">
    <table class="table">
      <thead class="table-header sticky top-0 bg-surface-elevated">
        <tr>
          <th class="pt-3 pr-4">{$t("network.protocol")}</th>
          <th class="pt-3 pr-4">{$t("network.connections.state")}</th>
          <th class="pt-3 pr-4">{$t("network.connections.local")}</th>
          <th class="pt-3">{$t("network.connections.remote")}</th>
        </tr>
      </thead>
      <tbody>
        {#each connections as conn}
          <tr class="table-row">
            <td class="pr-4">
              <Badge variant={conn.protocol === "tcp" ? "info" : "purple"}>{conn.protocol}</Badge>
            </td>
            <td class="pr-4">
              <span class="text-xs {conn.state === 'ESTAB' ? 'text-status-success' : 'text-content-secondary'}">
                {conn.state}
              </span>
            </td>
            <td class="pr-4 font-mono text-xs break-all">{conn.local}</td>
            <td class="font-mono text-xs break-all">{conn.remote || "-"}</td>
          </tr>
        {:else}
          <tr>
            <td colspan="4" class="py-8 text-center text-content-secondary">{$t("network.connections.none")}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</div>
