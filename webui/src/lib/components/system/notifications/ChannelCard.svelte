<script lang="ts">
  import { Pencil, Trash2, Zap } from "lucide-svelte";
  import type { NotificationConfig } from "$lib/api/notifications";
  import { Alert, Badge, Button, Card } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { KINDS, eventName, kindName, nameOf, summaryOf } from "./channels";

  // A channel of the list: its kind, name, settings and events, its actions, and the outcome of
  // its last test.
  type Props = {
    config: NotificationConfig;
    // The outcome of the channel's last test, until dismissed.
    result: { success: boolean; message: string } | undefined;
    // The id of the channel whose test runs.
    testing: string | null;
    ontest: (config: NotificationConfig) => void;
    ontoggle: (config: NotificationConfig) => void;
    onedit: (config: NotificationConfig) => void;
    ondelete: (config: NotificationConfig) => void;
    ondismissresult: (id: string) => void;
  };

  let { config, result, testing, ontest, ontoggle, onedit, ondelete, ondismissresult }: Props = $props();

  let id = $derived(config.id ?? "");
  let name = $derived(nameOf($t, config));
  let Icon = $derived(KINDS[config.channel].icon);
</script>

<Card>
  <div class="flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-4 min-w-0">
      <Icon
        class="w-6 h-6 flex-shrink-0 {config.enabled ? 'text-info' : 'text-muted'}"
      />
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-medium">{name}</h3>
          <Badge variant="neutral">{kindName($t, config.channel)}</Badge>
          {#if !config.enabled}
            <Badge variant="warning">{$t("notifications.channels.disabled")}</Badge>
          {/if}
        </div>
        <p class="text-sm text-secondary break-all">{summaryOf($t, config)}</p>
        {#if config.events && config.events.length > 0}
          <div class="flex flex-wrap gap-1 mt-2">
            {#each config.events.slice(0, 3) as event}
              <Badge variant="neutral">{eventName($t, event)}</Badge>
            {/each}
            {#if config.events.length > 3}
              <Badge variant="neutral">{$t("notifications.channels.more", { n: config.events.length - 3 })}</Badge>
            {/if}
          </div>
        {/if}
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        size="sm"
        icon={Zap}
        loading={testing === id}
        disabled={testing !== null && testing !== id}
        onclick={() => ontest(config)}
      >
        {$t("notifications.channels.sendTest")}<span class="sr-only"> {$t("notifications.channels.sendTestTo", { name })}</span>
      </Button>
      <!-- Not while the channel's test runs: its outcome would show under the new settings. -->
      <Button variant="ghost" size="sm" disabled={testing === id} onclick={() => ontoggle(config)}>
        {$t(config.enabled ? "notifications.channels.disable" : "notifications.channels.enable")}<span class="sr-only"> {$t("notifications.channels.named", { name })}</span>
      </Button>
      <Button
        variant="ghost"
        size="icon-sm"
        icon={Pencil}
        disabled={testing === id}
        onclick={() => onedit(config)}
      >
        <span class="sr-only">{$t("notifications.channels.edit", { name })}</span>
      </Button>
      <Button variant="ghost" size="icon-sm" icon={Trash2} onclick={() => ondelete(config)}>
        <span class="sr-only">{$t("notifications.channels.delete", { name })}</span>
      </Button>
    </div>
  </div>
  {#if result}
    <div class="mt-3" role={result.success ? "status" : "alert"}>
      <Alert
        variant={result.success ? "success" : "error"}
        title={result.success ? "" : $t("notifications.test.notSent")}
        dismissible
        ondismiss={() => ondismissresult(id)}
      >
        <span class="break-words">{result.message}</span>
      </Alert>
    </div>
  {/if}
</Card>
