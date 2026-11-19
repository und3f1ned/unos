<script lang="ts">
  import { Moon, Power, RotateCcw } from "lucide-svelte";
  import type { PendingAction } from "$lib/api/power";
  import { Alert, Button, Card } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatTimeOfDay } from "$lib/time";
  import type { Action } from "./actions";

  // Reboot, Shut down and Suspend, and the reboot or shutdown that waits for its minute, which can
  // still be cancelled.
  type Props = {
    pending: PendingAction | null;
    pendingError: string;
    cancelling: boolean;
    oncancel: () => void;
    // An action was asked: it waits for its confirmation.
    onask: (action: Action) => void;
  };

  let { pending, pendingError, cancelling, oncancel, onask }: Props = $props();

  // In the language of the page; reactive, so that a pending action follows a change of the language.
  let timeOf = $derived((at: string): string => formatTimeOfDay(at, $intlLocale, { hour: "2-digit", minute: "2-digit" }));
</script>

<Card>
  <h3 class="section-title mb-4">
    <Power class="w-5 h-5" />
    {$t("power.actions.title")}
  </h3>
  <div class="space-y-4">
    {#if pendingError}
      <Alert variant="error" title={$t("power.pending.readError")}>{pendingError}</Alert>
    {:else if pending?.action}
      {@const reboot = pending.action === "reboot"}
      <Alert variant="warning" title={reboot ? $t("power.pending.reboot") : $t("power.pending.shutdown")}>
        <div class="flex flex-wrap items-center gap-3">
          <p>
            {#if pending.at}
              {$t(reboot ? "power.pending.rebootAt" : "power.pending.shutdownAt", { time: timeOf(pending.at) })}
            {:else}
              {reboot ? $t("power.pending.rebootSoon") : $t("power.pending.shutdownSoon")}
            {/if}
          </p>
          <Button variant="secondary" size="sm" loading={cancelling} onclick={oncancel}>
            {reboot ? $t("power.pending.cancelReboot") : $t("power.pending.cancelShutdown")}
          </Button>
        </div>
      </Alert>
    {/if}
    <p class="text-sm text-secondary">{$t("power.actions.hint")}</p>
    <div class="flex flex-wrap gap-3">
      <Button variant="warning" icon={RotateCcw} onclick={() => onask("reboot")}>{$t("power.reboot.button")}</Button>
      <Button variant="danger" icon={Power} onclick={() => onask("shutdown")}>{$t("power.shutdown.button")}</Button>
      <Button variant="secondary" icon={Moon} onclick={() => onask("suspend")}>{$t("power.suspend.button")}</Button>
    </div>
  </div>
</Card>
