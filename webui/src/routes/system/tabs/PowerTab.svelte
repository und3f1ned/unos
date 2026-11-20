<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount, untrack } from "svelte";
  import { cancelShutdown, getPendingAction, type PendingAction } from "$lib/api/power";
  import { Alert, Button } from "$lib/components/ui";
  import {
    ACTIONS,
    PowerActionsCard,
    PowerConfirmDialog,
    PowerPlan,
    PowerScheduleCard,
    PowerStatusCard,
    REQUESTED,
    knownZone,
    WakeOnLan,
    WakeOnLanCard,
    type Action
  } from "$lib/components/system/power";
  import { t } from "$lib/i18n";
  import { paletteRequest, type PaletteRequest } from "$lib/stores/palette";

  // Reboot, shutdown, suspend and Wake-on-LAN act on the host (ADR-0233):
  // reboot and shutdown one minute after the confirmation, so that they can
  // still be cancelled here; suspend at once.

  let pending = $state<PendingAction | null>(null);
  let pendingKnown = $state(false);
  let pendingError = $state("");
  const wol = new WakeOnLan();

  let asking = $state<Action | null>(null);
  let busy = $state(false);
  let confirmError = $state("");
  let cancelling = $state(false);
  let notice = $state("");
  let actionError = $state("");

  // The scheduled shutdown, the wake by the RTC and the idle shutdown of the power settings
  // (ADR-0407).
  const plan = new PowerPlan();

  onMount(() => {
    void loadPending();
    void wol.load();
    void plan.loadConfig();
    void plan.loadSchedule();
  });

  /** What an action of a card did: the page says it, in place of an earlier failure. */
  function showNotice(message: string) {
    notice = message;
    actionError = "";
  }

  /** Why the host refused an action of a card, in place of an earlier notice. */
  function showFailure(message: string) {
    notice = "";
    actionError = message;
  }

  async function loadPending() {
    try {
      pending = await getPendingAction();
      pendingError = "";
    } catch (e) {
      pendingError = messageOf(e);
    } finally {
      pendingKnown = true;
    }
  }

  /** Every read again, after the host's power state could not be read. */
  async function retry() {
    await Promise.all([loadPending(), wol.load(), plan.reload()]);
  }

  function ask(action: Action) {
    asking = action;
    confirmError = "";
  }

  // The command palette's Reboot, Shut down and Suspend open their confirmation here, which acts
  // only once confirmed (ADR-0393). Before the render, as the request may be there when the tab
  // mounts; untracked, so that the effect depends on the request alone.
  $effect.pre(() => {
    const request = $paletteRequest;
    untrack(() => openRequested(request));
  });

  function openRequested(request: PaletteRequest | null) {
    const action = request ? REQUESTED[request.dialog] : undefined;
    if (!action) return;
    paletteRequest.set(null);
    if (!busy) ask(action);
  }

  function closeConfirm() {
    if (!busy) {
      asking = null;
      confirmError = "";
    }
  }

  async function confirmAction() {
    if (!asking || busy) return;
    const action = ACTIONS[asking];
    busy = true;
    confirmError = "";
    try {
      const answer = await action.run();
      showNotice(answer.message);
      if (answer.pending) {
        pending = answer.pending;
        pendingError = "";
      }
      asking = null;
    } catch (e) {
      // The dialog stays open with the host's answer.
      confirmError = messageOf(e);
    } finally {
      busy = false;
    }
  }

  async function cancelPending() {
    if (cancelling) return;
    cancelling = true;
    try {
      const answer = await cancelShutdown();
      showNotice(answer.message);
      pending = answer.pending;
    } catch (e) {
      showFailure(messageOf(e));
      await loadPending();
    } finally {
      cancelling = false;
    }
  }

  let confirmation = $derived(asking ? ACTIONS[asking] : null);

  // One reason for every card: when the host's power state cannot be read (no host commands, a
  // host that does not answer), the pending action, the plan, the settings and the interfaces fail
  // alike. The
  // page says it once, with Retry, and the cards only that they wait.
  let hostError = $derived.by(() => {
    const failed = [pendingError, plan.scheduleError, plan.configError, wol.error].filter(Boolean);
    return failed.length >= 2 && failed.every((message) => message === failed[0]) ? failed[0] : "";
  });
  // The times of the tab are on the NAS's clock, the one its settings are on; named once.
  let zone = $derived(knownZone(plan.schedule?.timezone));
</script>

<div class="space-y-6">
  <div>
    <h2 class="text-xl font-semibold">{$t("power.title")}</h2>
    {#if zone}
      <p class="page-subtitle" data-testid="power-zone">{$t("power.zone", { zone })}</p>
    {/if}
  </div>

  {#if hostError}
    <div role="alert">
      <Alert variant="error" title={$t("power.hostError")}>
        <p class="break-words">{hostError}</p>
        <Button variant="secondary" size="sm" class="mt-2" onclick={retry}>{$t("common.retry")}</Button>
      </Alert>
    </div>
  {/if}
  {#if notice}
    <div role="status">
      <Alert variant="success" dismissible ondismiss={() => (notice = "")}>{notice}</Alert>
    </div>
  {/if}
  {#if actionError}
    <div role="alert">
      <Alert variant="error" dismissible ondismiss={() => (actionError = "")}>{actionError}</Alert>
    </div>
  {/if}

  <PowerStatusCard
    {pending}
    {pendingKnown}
    pendingError={hostError ? "" : pendingError}
    schedule={plan.schedule}
    scheduleError={hostError ? "" : plan.scheduleError}
    {zone}
    unavailable={hostError !== "" && plan.scheduleError === hostError}
    {cancelling}
    oncancel={cancelPending}
  />

  <PowerActionsCard unavailable={pendingError !== ""} onask={ask} />

  <PowerScheduleCard {plan} unavailable={hostError !== "" && plan.configError === hostError} onnotice={showNotice} />

  <WakeOnLanCard {wol} unavailable={hostError !== "" && wol.error === hostError} onnotice={showNotice} onfailure={showFailure} />
</div>

<PowerConfirmDialog {confirmation} {busy} error={confirmError} onclose={closeConfirm} onconfirm={confirmAction} />
