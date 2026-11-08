<script lang="ts">
  import { onMount } from "svelte";
  import { Moon, Network, Power, RotateCcw } from "lucide-svelte";
  import {
    cancelShutdown,
    disableWol,
    enableWol,
    getPendingAction,
    getWolStatus,
    rebootSystem,
    shutdownSystem,
    suspendSystem,
    type PendingAction,
    type PowerAnswer,
    type WolInterface,
  } from "$lib/api/power";
  import { Alert, Badge, Button, Card, EmptyState, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";

  // Reboot, shutdown, suspend and Wake-on-LAN act on the host (ADR-0233):
  // reboot and shutdown one minute after the confirmation, so that they can
  // still be cancelled here; suspend at once.

  type Action = "reboot" | "shutdown" | "suspend";

  // The confirmation of each action: the keys of its texts in the catalog.
  const ACTIONS: Record<
    Action,
    { titleKey: string; textKey: string; confirmKey: string; failedKey: string; variant: "warning" | "danger"; run: () => Promise<PowerAnswer> }
  > = {
    reboot: {
      titleKey: "power.reboot.title",
      textKey: "power.reboot.text",
      confirmKey: "power.reboot.confirm",
      failedKey: "power.reboot.failed",
      variant: "warning",
      run: rebootSystem,
    },
    shutdown: {
      titleKey: "power.shutdown.title",
      textKey: "power.shutdown.text",
      confirmKey: "power.shutdown.confirm",
      failedKey: "power.shutdown.failed",
      variant: "danger",
      run: shutdownSystem,
    },
    suspend: {
      titleKey: "power.suspend.title",
      textKey: "power.suspend.text",
      confirmKey: "power.suspend.confirm",
      failedKey: "power.suspend.failed",
      variant: "danger",
      run: suspendSystem,
    },
  };

  let pending: PendingAction | null = null;
  let pendingError = "";
  let interfaces: WolInterface[] = [];
  let wolLoading = true;
  let wolError = "";

  let asking: Action | null = null;
  let busy = false;
  let confirmError = "";
  let cancelling = false;
  let wolBusy: string | null = null;
  let notice = "";
  let actionError = "";

  onMount(() => {
    void loadPending();
    void loadWol();
  });

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadPending() {
    try {
      pending = await getPendingAction();
      pendingError = "";
    } catch (e) {
      pendingError = messageOf(e);
    }
  }

  async function loadWol() {
    try {
      interfaces = (await getWolStatus()).interfaces;
      wolError = "";
    } catch (e) {
      wolError = messageOf(e);
    } finally {
      wolLoading = false;
    }
  }

  function ask(action: Action) {
    asking = action;
    confirmError = "";
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
      notice = answer.message;
      actionError = "";
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
      notice = answer.message;
      actionError = "";
      pending = answer.pending;
    } catch (e) {
      notice = "";
      actionError = messageOf(e);
      await loadPending();
    } finally {
      cancelling = false;
    }
  }

  async function setWol(iface: WolInterface, on: boolean) {
    if (wolBusy) return;
    wolBusy = iface.name;
    try {
      const updated = on ? await enableWol(iface.name) : await disableWol(iface.name);
      interfaces = interfaces.map((i) => (i.name === updated.name ? updated : i));
      notice = $t(updated.enabled ? "power.wol.turnedOn" : "power.wol.turnedOff", { name: updated.name });
      actionError = "";
    } catch (e) {
      notice = "";
      actionError = messageOf(e);
      // Show what the host has now.
      await loadWol();
    } finally {
      wolBusy = null;
    }
  }

  // In the language of the page; reactive, so that a pending action follows a change of the language.
  $: timeOf = (at: string): string =>
    new Date(at).toLocaleTimeString($intlLocale, { hour: "2-digit", minute: "2-digit" });

  $: confirmation = asking ? ACTIONS[asking] : null;
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">{$t("power.title")}</h2>

  {#if notice}
    <div role="status">
      <Alert variant="success" dismissible on:dismiss={() => (notice = "")}>{notice}</Alert>
    </div>
  {/if}
  {#if actionError}
    <div role="alert">
      <Alert variant="error" dismissible on:dismiss={() => (actionError = "")}>{actionError}</Alert>
    </div>
  {/if}

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
            <Button variant="secondary" size="sm" loading={cancelling} on:click={cancelPending}>
              {reboot ? $t("power.pending.cancelReboot") : $t("power.pending.cancelShutdown")}
            </Button>
          </div>
        </Alert>
      {/if}
      <p class="text-sm text-secondary">{$t("power.actions.hint")}</p>
      <div class="flex flex-wrap gap-3">
        <Button variant="warning" icon={RotateCcw} on:click={() => ask("reboot")}>{$t("power.reboot.button")}</Button>
        <Button variant="danger" icon={Power} on:click={() => ask("shutdown")}>{$t("power.shutdown.button")}</Button>
        <Button variant="secondary" icon={Moon} on:click={() => ask("suspend")}>{$t("power.suspend.button")}</Button>
      </div>
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <Network class="w-5 h-5" />
      {$t("power.wol.title")}
    </h3>
    {#if wolLoading}
      <p class="text-sm text-muted">{$t("common.loading")}</p>
    {:else if wolError}
      <Alert variant="error" title={$t("power.wol.loadError")}>{wolError}</Alert>
    {:else if interfaces.length === 0}
      <EmptyState icon={Network} title={$t("power.wol.none")} />
    {:else}
      <div class="table-frame">
        <table class="table">
          <thead class="table-header">
            <tr>
              <th>{$t("power.wol.interface")}</th>
              <th>{$t("power.wol.mac")}</th>
              <th>{$t("power.wol.title")}</th>
              <th><span class="sr-only">{$t("power.wol.change")}</span></th>
            </tr>
          </thead>
          <tbody>
            {#each interfaces as iface (iface.name)}
              <tr class="table-row">
                <td class="mono">{iface.name}</td>
                <td class="mono">{iface.mac_address ?? $t("power.wol.unknownMac")}</td>
                <td>
                  {#if !iface.supported}
                    <Badge variant="neutral">{$t("power.wol.notSupported")}</Badge>
                  {:else if iface.enabled}
                    <Badge variant="success">{$t("power.wol.on")}</Badge>
                  {:else}
                    <Badge variant="neutral">{$t("power.wol.off")}</Badge>
                  {/if}
                </td>
                <td class="text-right">
                  {#if iface.supported}
                    <Button
                      variant="secondary"
                      size="sm"
                      loading={wolBusy === iface.name}
                      disabled={wolBusy !== null && wolBusy !== iface.name}
                      on:click={() => setWol(iface, !iface.enabled)}
                    >
                      {iface.enabled ? $t("power.wol.turnOff") : $t("power.wol.turnOn")}<span class="sr-only"
                        >{$t("power.wol.forInterface", { name: iface.name })}</span
                      >
                    </Button>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="text-sm text-muted mt-3">
        {$t("power.wol.hintBefore")}
        <span class="mono">{$t("power.wol.command")}</span>{$t("power.wol.hintAfter")}
      </p>
    {/if}
  </Card>
</div>

<Modal
  open={confirmation !== null}
  title={confirmation ? $t(confirmation.titleKey) : ""}
  size="md"
  danger
  closable={!busy}
  on:close={closeConfirm}
>
  <p>{confirmation ? $t(confirmation.textKey) : ""}</p>
  {#if confirmError && confirmation}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={$t(confirmation.failedKey)}>{confirmError}</Alert>
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>{$t("common.cancel")}</Button>
    <Button variant={confirmation?.variant ?? "danger"} class="flex-1" loading={busy} on:click={confirmAction}>
      {confirmation ? $t(confirmation.confirmKey) : ""}
    </Button>
  </svelte:fragment>
</Modal>
