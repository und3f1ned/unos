<script lang="ts">
  import type { UpdateHistoryEntry } from "$lib/api/updates";
  import { Badge, Card } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";

  // The updates that ran, newest first (GET /updates/history, ADR-0443), on the NAS's clock.
  type Props = {
    history: UpdateHistoryEntry[];
    zone: string | null;
  };

  let { history, zone }: Props = $props();
</script>

<Card title={$t("updates.history.title")} flush>
  <div class="table-frame">
    <table class="table table-compact table-stack">
      <thead class="table-header">
        <tr>
          <th>{$t("updates.history.date")}</th>
          <th>{$t("updates.history.from")}</th>
          <th>{$t("updates.history.to")}</th>
          <th>{$t("updates.history.result")}</th>
        </tr>
      </thead>
      <tbody>
        {#each history as entry (entry.id)}
          <tr class="table-row">
            <td data-label={$t("updates.history.date")} class="tabular">
              {formatDateTime(entry.started_at, $intlLocale, { dateStyle: "medium", timeStyle: "short", zone })}
            </td>
            <td data-label={$t("updates.history.from")} class="mono break-all">{entry.from_version}</td>
            <td data-label={$t("updates.history.to")} class="mono break-all">{entry.to_version}</td>
            <td data-label={$t("updates.history.result")}>
              {#if entry.status === "success"}
                <Badge variant="success">{$t("updates.history.success")}</Badge>
              {:else}
                <Badge variant="error">{$t("updates.history.failed")}</Badge>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</Card>
