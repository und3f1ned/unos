<script lang="ts">
  import { onMount, untrack } from "svelte";
  import { cancelShutdown, getPendingAction, type PendingAction } from "$lib/api/power";
  import { Alert } from "$lib/components/ui";
  import {
    ACTIONS,
    PowerActionsCard,
    PowerConfirmDialog,
    PowerPlan,
    PowerScheduleCard,
    REQUESTED,
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

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

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
    }
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
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">{$t("power.title")}</h2>

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

  <PowerActionsCard {pending} {pendingError} {cancelling} oncancel={cancelPending} onask={ask} />

  <PowerScheduleCard {plan} onnotice={showNotice} />

  <WakeOnLanCard {wol} onnotice={showNotice} onfailure={showFailure} />
</div>

<PowerConfirmDialog {confirmation} {busy} error={confirmError} onclose={closeConfirm} onconfirm={confirmAction} />
