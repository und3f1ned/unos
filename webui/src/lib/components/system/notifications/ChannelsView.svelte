<script lang="ts">
  import { Bell, Plus } from "lucide-svelte";
  import type { NotificationConfig } from "$lib/api/notifications";
  import { Button, Card, EmptyState, Skeleton } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import ChannelCard from "./ChannelCard.svelte";

  // The Channels view: the channels, or an empty state that adds the first one (the tab's header
  // adds the others).
  type Props = {
    configs: NotificationConfig[];
    loading: boolean;
    // The outcome of each channel's last test, by channel id.
    results: Record<string, { success: boolean; message: string }>;
    // The id of the channel whose test runs.
    testing: string | null;
    // The ids of the channels whose on/off the server has not answered yet.
    toggling: ReadonlySet<string>;
    oncreate: () => void;
    ontest: (config: NotificationConfig) => void;
    ontoggle: (config: NotificationConfig) => void;
    onedit: (config: NotificationConfig) => void;
    ondelete: (config: NotificationConfig) => void;
    ondismissresult: (id: string) => void;
  };

  let { configs, loading, results, testing, toggling, oncreate, ontest, ontoggle, onedit, ondelete, ondismissresult }: Props =
    $props();
</script>

<div class="space-y-4">
  {#if loading && configs.length === 0}
    <div class="space-y-4" aria-busy="true">
      <Skeleton shape="card" count={2} />
      <Skeleton shape="card" count={2} />
    </div>
  {:else if configs.length === 0}
    <Card>
      <EmptyState
        icon={Bell}
        title={$t("notifications.channels.none")}
        description={$t("notifications.channels.noneHint")}
      >
        <Button variant="secondary" icon={Plus} onclick={oncreate}>{$t("notifications.addFirst")}</Button>
      </EmptyState>
    </Card>
  {:else}
    <ul class="space-y-3" aria-label={$t("notifications.channels.list")}>
      {#each configs as config (config.id)}
        <li>
          <ChannelCard
            {config}
            result={results[config.id ?? ""]}
            {testing}
            toggling={toggling.has(config.id ?? "")}
            {ontest}
            {ontoggle}
            {onedit}
            {ondelete}
            {ondismissresult}
          />
        </li>
      {/each}
    </ul>
  {/if}
</div>
