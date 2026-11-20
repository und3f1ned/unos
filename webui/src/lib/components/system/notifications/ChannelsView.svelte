<script lang="ts">
  import { Bell } from "lucide-svelte";
  import type { ChannelKind, NotificationConfig } from "$lib/api/notifications";
  import { Button, Card, EmptyState, Skeleton } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import ChannelCard from "./ChannelCard.svelte";
  import { KINDS, KIND_ORDER } from "./channels";

  // The Channels view: a button that adds a channel of each kind, and the channels.
  type Props = {
    configs: NotificationConfig[];
    loading: boolean;
    // The outcome of each channel's last test, by channel id.
    results: Record<string, { success: boolean; message: string }>;
    // The id of the channel whose test runs.
    testing: string | null;
    oncreate: (kind: ChannelKind) => void;
    ontest: (config: NotificationConfig) => void;
    ontoggle: (config: NotificationConfig) => void;
    onedit: (config: NotificationConfig) => void;
    ondelete: (config: NotificationConfig) => void;
    ondismissresult: (id: string) => void;
  };

  let { configs, loading, results, testing, oncreate, ontest, ontoggle, onedit, ondelete, ondismissresult }: Props =
    $props();
</script>

<div class="space-y-4">
  <div class="flex flex-wrap gap-3">
    {#each KIND_ORDER as kind}
      <Button variant="secondary" icon={KINDS[kind].icon} onclick={() => oncreate(kind)}>
        {$t(KINDS[kind].add)}
      </Button>
    {/each}
  </div>

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
      />
    </Card>
  {:else}
    <ul class="space-y-3" aria-label={$t("notifications.channels.list")}>
      {#each configs as config (config.id)}
        <li>
          <ChannelCard
            {config}
            result={results[config.id ?? ""]}
            {testing}
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
