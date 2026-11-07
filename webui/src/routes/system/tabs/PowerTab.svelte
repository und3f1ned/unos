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

  // Reboot, shutdown, suspend and Wake-on-LAN act on the host (ADR-0232):
  // reboot and shutdown one minute after the confirmation, so that they can
  // still be cancelled here; suspend at once.

  type Action = "reboot" | "shutdown" | "suspend";

  const ACTIONS: Record<
    Action,
    { title: string; text: string; confirm: string; failed: string; variant: "warning" | "danger"; run: () => Promise<PowerAnswer> }
  > = {
    reboot: {
      title: "Reboot the host?",
      text: "The host reboots one minute after you confirm; until then you can cancel the reboot here. Shares, apps and this page are unavailable until the host is back.",
      confirm: "Reboot in 1 minute",
      failed: "The reboot was not scheduled",
      variant: "warning",
      run: rebootSystem,
    },
    shutdown: {
      title: "Shut down the host?",
      text: "The host shuts down one minute after you confirm; until then you can cancel the shutdown here. It starts again only with its power button or a Wake-on-LAN packet.",
      confirm: "Shut down in 1 minute",
      failed: "The shutdown was not scheduled",
      variant: "danger",
      run: shutdownSystem,
    },
    suspend: {
      title: "Suspend the host?",
      text: "The host suspends at once and sleeps until its power button or a Wake-on-LAN packet wakes it. Shares, apps and this page are unavailable meanwhile.",
      confirm: "Suspend now",
      failed: "The host was not suspended",
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
      notice = `Wake-on-LAN is ${updated.enabled ? "on" : "off"} for ${updated.name}`;
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

  function timeOf(at: string): string {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  $: confirmation = asking ? ACTIONS[asking] : null;
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">Power Management</h2>

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
      Reboot, shutdown and suspend
    </h3>
    <div class="space-y-4">
      {#if pendingError}
        <Alert variant="error" title="The pending reboot or shutdown could not be read">{pendingError}</Alert>
      {:else if pending?.action}
        <Alert variant="warning" title={pending.action === "reboot" ? "A reboot is pending" : "A shutdown is pending"}>
          <div class="flex flex-wrap items-center gap-3">
            <p>
              The host {pending.action === "reboot" ? "reboots" : "shuts down"}
              {pending.at ? `at ${timeOf(pending.at)}` : "within a minute"}.
            </p>
            <Button variant="secondary" size="sm" loading={cancelling} on:click={cancelPending}>
              Cancel {pending.action}
            </Button>
          </div>
        </Alert>
      {/if}
      <p class="text-sm text-secondary">
        Reboot and Shutdown act on the host one minute after you confirm, and you can cancel them here
        until then. Suspend acts at once.
      </p>
      <div class="flex flex-wrap gap-3">
        <Button variant="warning" icon={RotateCcw} on:click={() => ask("reboot")}>Reboot</Button>
        <Button variant="danger" icon={Power} on:click={() => ask("shutdown")}>Shutdown</Button>
        <Button variant="secondary" icon={Moon} on:click={() => ask("suspend")}>Suspend</Button>
      </div>
    </div>
  </Card>

  <Card>
    <h3 class="section-title mb-4">
      <Network class="w-5 h-5" />
      Wake-on-LAN
    </h3>
    {#if wolLoading}
      <p class="text-sm text-muted">Loading...</p>
    {:else if wolError}
      <Alert variant="error" title="The network interfaces could not be read">{wolError}</Alert>
    {:else if interfaces.length === 0}
      <EmptyState icon={Network} title="The host has no Ethernet interface" />
    {:else}
      <div class="overflow-x-auto">
        <table class="table">
          <thead class="table-header">
            <tr>
              <th>Interface</th>
              <th>MAC address</th>
              <th>Wake-on-LAN</th>
              <th><span class="sr-only">Change</span></th>
            </tr>
          </thead>
          <tbody>
            {#each interfaces as iface (iface.name)}
              <tr class="table-row">
                <td class="mono">{iface.name}</td>
                <td class="mono">{iface.mac_address ?? "unknown"}</td>
                <td>
                  {#if !iface.supported}
                    <Badge variant="neutral">Not supported</Badge>
                  {:else if iface.enabled}
                    <Badge variant="success">On</Badge>
                  {:else}
                    <Badge variant="neutral">Off</Badge>
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
                      {iface.enabled ? "Turn off" : "Turn on"}<span class="sr-only"> Wake-on-LAN for {iface.name}</span>
                    </Button>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="text-sm text-muted mt-3">
        With Wake-on-LAN on, a magic packet to the interface's MAC address, sent from a computer on the
        same network (for example <span class="mono">wakeonlan &lt;MAC address&gt;</span>), starts the host
        after a shutdown and wakes it from suspend. UNOS sets your choice again whenever it starts.
      </p>
    {/if}
  </Card>
</div>

<Modal
  open={confirmation !== null}
  title={confirmation?.title ?? ""}
  size="md"
  danger
  closable={!busy}
  on:close={closeConfirm}
>
  <p>{confirmation?.text ?? ""}</p>
  {#if confirmError && confirmation}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={confirmation.failed}>{confirmError}</Alert>
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={busy} on:click={closeConfirm}>Cancel</Button>
    <Button variant={confirmation?.variant ?? "danger"} class="flex-1" loading={busy} on:click={confirmAction}>
      {confirmation?.confirm ?? ""}
    </Button>
  </svelte:fragment>
</Modal>
