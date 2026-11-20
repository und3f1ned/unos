<script lang="ts">
  import { Pencil, Trash2, Zap } from "lucide-svelte";
  import type { NotificationConfig } from "$lib/api/notifications";
  import { Alert, Badge, Button, Card } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { KINDS, kindName, nameOf, summaryOf } from "./channels";

  // A channel of the list: its kind, name and settings, its actions, and the outcome of its last
  // test. Its events are not shown: alerts go to every enabled channel whatever they name.
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
      <Icon class="w-5 h-5 flex-shrink-0 {config.enabled ? 'text-content-secondary' : 'text-content-muted'}" aria-hidden="true" />
      <div class="min-w-0">
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-medium">{name}</h3>
          <Badge variant="neutral">{kindName($t, config.channel)}</Badge>
          {#if !config.enabled}
            <Badge variant="neutral">{$t("notifications.channels.disabled")}</Badge>
          {/if}
        </div>
        <p class="text-sm text-content-secondary break-words">{summaryOf($t, config)}</p>
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
      <!-- The channel's on/off, a checkbox in the place of the design system's Switch.
           Not while the channel's test runs: its outcome would show under the new settings. -->
      <label class="flex items-center gap-2 px-2 text-sm text-content-secondary {testing === id ? 'opacity-50' : 'cursor-pointer'}">
        <input
          type="checkbox"
          class="form-checkbox h-4 w-4"
          checked={config.enabled}
          disabled={testing === id}
          aria-label={$t("notifications.channels.toggle", { name })}
          onchange={(e) => {
            // The box shows the stored state: it changes once the server took the change.
            e.currentTarget.checked = config.enabled;
            ontoggle(config);
          }}
        />
        {$t("notifications.channels.sendAlerts")}
      </label>
      <Button
        variant="ghost"
        size="icon-sm"
        icon={Pencil}
        disabled={testing === id}
        title={$t("notifications.channels.edit", { name })}
        onclick={() => onedit(config)}
      >
        <span class="sr-only">{$t("notifications.channels.edit", { name })}</span>
      </Button>
      <Button variant="ghost-danger" size="icon-sm" icon={Trash2} title={$t("notifications.channels.delete", { name })} onclick={() => ondelete(config)}>
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
