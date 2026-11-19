<script lang="ts">
  import { onMount } from "svelte";
  import { Clock, RefreshCw, Settings } from "lucide-svelte";
  import { Alert, Button, type IconComponent } from "$lib/components/ui";
  import {
    ChannelDialog,
    ChannelForm,
    ChannelsView,
    DeleteChannelDialog,
    HistoryFilter,
    HistoryView,
    NotificationStats
  } from "$lib/components/system/notifications";
  import { kindName } from "$lib/components/system/notifications/channels";
  import { t } from "$lib/i18n";
  import {
    createConfig, getConfigs, getEventTypes, getLogs, testNotification, updateConfig,
    type ChannelKind, type EventType, type NotificationConfig, type NotificationLog
  } from "$lib/api/notifications";

  // Channels of five kinds (ADR-0326). A test reports next to its channel whether the server
  // delivered it, or why not, and the history lists every notification sent, newest first.

  type View = "channels" | "history";

  let logs = $state<NotificationLog[]>([]);
  let configs = $state<NotificationConfig[]>([]);
  let eventTypes = $state<EventType[]>([]);
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

  let successCount = $derived(logs.filter((l) => l.success).length);
  let failedCount = $derived(logs.filter((l) => !l.success).length);
  let activeChannels = $derived(configs.filter((c) => c.enabled).length);

  onMount(() => loadData());

  function messageOf(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
  }

  async function loadData() {
    loading = true;
    try {
      [logs, configs, eventTypes] = await Promise.all([getLogs(), getConfigs(), getEventTypes()]);
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

  function openCreate(kind: ChannelKind) {
    form.openCreate(kind, $t("notifications.form.defaultName", { kind: kindName($t, kind) }));
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

<!-- A toggle of the view shown. -->
{#snippet viewButton(view: View, icon: IconComponent, label: string)}
  <Button
    variant={activeView === view ? "primary" : "secondary"}
    {icon}
    pressed={activeView === view}
    onclick={() => (activeView = view)}
  >
    {label}
  </Button>
{/snippet}

<div class="space-y-6">
  <NotificationStats active={activeChannels} sent={logs.length} delivered={successCount} failed={failedCount} />

  <div class="flex items-center justify-between gap-3">
    <div class="flex gap-2">
      {@render viewButton("channels", Settings, $t("notifications.views.channels"))}
      {@render viewButton("history", Clock, $t("notifications.views.history"))}
    </div>
    <Button variant="ghost" size="icon" icon={RefreshCw} loading={loading} onclick={loadData}>
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

<ChannelDialog {form} {eventTypes} onsave={handleSave} />

<DeleteChannelDialog channel={deleting} onclose={() => (deleting = null)} ondeleted={deleted} />
