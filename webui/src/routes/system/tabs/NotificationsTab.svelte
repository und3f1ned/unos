<script lang="ts">
  import { messageOf } from "$lib/errors";
  import { onMount } from "svelte";
  import { Plus, RefreshCw } from "lucide-svelte";
  import { Alert, Button } from "$lib/components/ui";
  import {
    ChannelDialog,
    ChannelForm,
    ChannelsView,
    DeleteChannelDialog,
    HistoryFilter,
    HistoryView
  } from "$lib/components/system/notifications";
  import { kindName } from "$lib/components/system/notifications/channels";
  import { t } from "$lib/i18n";
  import {
    createConfig, getConfigs, getLogs, testNotification, updateConfig,
    type ChannelKind, type NotificationConfig, type NotificationLog
  } from "$lib/api/notifications";

  // Channels of five kinds (ADR-0326), each of which receives every alert. A test reports next to
  // its channel whether the server delivered it, or why not, and the history lists every
  // notification sent, newest first. A channel's events are not offered: the server sends every
  // alert to every enabled channel whatever they name.

  type View = "channels" | "history";

  let logs = $state<NotificationLog[]>([]);
  let configs = $state<NotificationConfig[]>([]);
  let loading = $state(true);
  let loadError = $state("");
  let activeView = $state<View>("channels");

  // The outcome of each channel's last test, by channel id: shown under the channel.
  let results = $state<Record<string, { success: boolean; message: string }>>({});
  let testing = $state<string | null>(null);
  let actionError = $state("");

  // The dialog that adds or edits a channel, and the channel whose deletion waits for its
  // confirmation.
  const form = new ChannelForm();
  let deleting = $state<NotificationConfig | null>(null);

  const filter = new HistoryFilter();

  onMount(() => loadData());

  async function loadData() {
    loading = true;
    try {
      [logs, configs] = await Promise.all([getLogs(), getConfigs()]);
      loadError = "";
    } catch (e) {
      loadError = messageOf(e);
    } finally {
      loading = false;
    }
  }

  async function refreshLogs() {
    try {
      logs = await getLogs();
    } catch (e) {
      loadError = messageOf(e);
    }
  }

  /** The name a new channel of `kind` gets until it is given one. */
  function defaultName(kind: ChannelKind): string {
    return $t("notifications.form.defaultName", { kind: kindName($t, kind) });
  }

  function openCreate() {
    form.openCreate("email", defaultName("email"));
  }

  async function handleSave() {
    if (form.saving || !form.complete) return;
    form.saving = true;
    form.saveError = "";
    try {
      const editing = form.editing;
      if (editing?.id) {
        await updateConfig(editing.id, form.config());
        // A test of the old settings says nothing about the new ones.
        dismissResult(editing.id);
      } else {
        await createConfig(form.config());
      }
      form.saving = false;
      form.close();
      await loadData();
    } catch (e) {
      form.saveError = messageOf(e);
    } finally {
      form.saving = false;
    }
  }

  async function handleTest(config: NotificationConfig) {
    const id = config.id;
    if (!id || testing) return;
    testing = id;
    dismissResult(id);
    try {
      await testNotification(id);
      results = { ...results, [id]: { success: true, message: $t("notifications.test.sent") } };
    } catch (e) {
      results = { ...results, [id]: { success: false, message: messageOf(e) } };
    } finally {
      testing = null;
    }
    // The test is the newest entry of the history now.
    await refreshLogs();
  }

  function dismissResult(id: string) {
    const { [id]: _, ...others } = results;
    results = others;
  }

  async function toggleEnabled(config: NotificationConfig) {
    if (!config.id) return;
    try {
      await updateConfig(config.id, { ...config, enabled: !config.enabled });
      actionError = "";
    } catch (e) {
      actionError = messageOf(e);
    }
    await loadData();
  }

  async function deleted(id: string) {
    dismissResult(id);
    deleting = null;
    await loadData();
  }
</script>

<!-- A view of the tab: Channels or History. -->
{#snippet viewTab(view: View, label: string)}
  <Button
    variant="plain"
    class="tab-underline {activeView === view ? 'tab-underline-active' : ''}"
    pressed={activeView === view}
    onclick={() => (activeView = view)}
  >
    {label}
  </Button>
{/snippet}

<!-- A tab of System's Settings: the channels the NAS notifies through, and what it sent (ADR-0431). -->
<div class="space-y-6">
  <div class="flex flex-wrap items-start justify-between gap-3">
    <div class="min-w-0">
      <h2 class="text-xl font-semibold">{$t("system.tabs.notifications")}</h2>
      <p class="page-subtitle">{$t("notifications.subtitle")}</p>
    </div>
    <Button variant="primary" icon={Plus} onclick={openCreate}>{$t("notifications.addChannel")}</Button>
  </div>

  <div class="tab-bar items-center">
    {@render viewTab("channels", $t("notifications.views.channels"))}
    {@render viewTab("history", $t("notifications.views.history"))}
    <Button variant="ghost" size="icon-sm" class="ml-auto" icon={RefreshCw} loading={loading}
      title={$t("common.refresh")} onclick={loadData}>
      <span class="sr-only">{$t("common.refresh")}</span>
    </Button>
  </div>

  {#if loadError}
    <div role="alert">
      <Alert variant="error" title={$t("notifications.loadError")}>{loadError}</Alert>
    </div>
  {/if}
  {#if actionError}
    <div role="alert">
      <Alert variant="error" dismissible ondismiss={() => (actionError = "")}>{actionError}</Alert>
    </div>
  {/if}

  {#if activeView === "channels"}
    <ChannelsView
      {configs}
      {loading}
      {results}
      {testing}
      oncreate={openCreate}
      ontest={handleTest}
      ontoggle={toggleEnabled}
      onedit={(config) => form.openEdit(config)}
      ondelete={(config) => (deleting = config)}
      ondismissresult={dismissResult}
    />
  {/if}

  {#if activeView === "history"}
    <HistoryView {logs} {loading} {filter} />
  {/if}
</div>

<ChannelDialog {form} {defaultName} onsave={handleSave} />

<DeleteChannelDialog channel={deleting} onclose={() => (deleting = null)} ondeleted={deleted} />
