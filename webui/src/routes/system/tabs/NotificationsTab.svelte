<script lang="ts">
  import { onMount } from "svelte";
  import type { ComponentType } from "svelte";
  import {
    AlertTriangle, Bell, CheckCircle, ChevronDown, ChevronUp, Clock, Info, Mail, MessageCircle,
    MessageSquare, Pencil, RefreshCw, Send, Settings, Slack, Trash2, Webhook, Zap
  } from "lucide-svelte";
  import { Alert, Badge, Button, Card, EmptyState, Input, Modal, Select } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { apiTime, formatDateTime } from "$lib/time";
  import {
    createConfig, deleteConfig, getConfigs, getEventTypes, getLogs, testNotification, updateConfig,
    type ChannelKind, type EventType, type NotificationConfig, type NotificationLog
  } from "$lib/api/notifications";

  // Channels of five kinds (ADR-0326). A test reports next to its channel whether the server
  // delivered it, or why not, and the history lists every notification sent, newest first.

  // Each kind's name and the text of the button that adds one: keys of the catalog.
  const KINDS: Record<ChannelKind, { name: string; add: string; icon: ComponentType }> = {
    email: { name: "notifications.kinds.email", add: "notifications.add.email", icon: Mail },
    telegram: { name: "notifications.kinds.telegram", add: "notifications.add.telegram", icon: MessageCircle },
    webhook: { name: "notifications.kinds.webhook", add: "notifications.add.webhook", icon: Webhook },
    discord: { name: "notifications.kinds.discord", add: "notifications.add.discord", icon: MessageSquare },
    slack: { name: "notifications.kinds.slack", add: "notifications.add.slack", icon: Slack },
  };
  const KIND_ORDER: ChannelKind[] = ["email", "telegram", "webhook", "discord", "slack"];
  // The URL field of the kinds that have one: its label and hint (keys of the catalog), and an example.
  const HOOK_FIELDS: Record<"webhook" | "discord" | "slack", { labelKey: string; hintKey: string; example: string }> = {
    webhook: {
      labelKey: "notifications.hook.url",
      hintKey: "notifications.hook.webhookHint",
      example: "https://example.com/hooks/unos",
    },
    discord: {
      labelKey: "notifications.hook.webhookUrl",
      hintKey: "notifications.hook.discordHint",
      example: "https://discord.com/api/webhooks/...",
    },
    slack: {
      labelKey: "notifications.hook.webhookUrl",
      hintKey: "notifications.hook.slackHint",
      example: "https://hooks.slack.com/services/...",
    },
  };
  // i18n-ignore: examples of a mail server, addresses and a bot token, the same in every language
  const EXAMPLES = {
    host: "smtp.gmail.com",
    user: "user@example.com",
    from: "noreply@example.com",
    to: "admin@example.com, user@example.com",
    token: "123456:ABC-DEF...",
    chat: "-1001234567890",
  };
  // i18n-ignore: HTTP methods, the same in every language
  const METHODS = [{ value: "POST", label: "POST" }, { value: "PUT", label: "PUT" }];
  // The types of notification (NotificationType of the server): keys of the catalog.
  const TYPES: Record<string, string> = {
    info: "notifications.types.info",
    warning: "notifications.types.warning",
    error: "notifications.types.error",
    success: "notifications.types.success",
  };
  // The events of the server's list (GET /api/v1/notifications/events), by id: the keys of their
  // names and descriptions, for the dialog and a channel's events in the list. An event without keys
  // shows the server's texts, or its id in the list. supervisor/tests/test_notifications.py checks
  // that the English half of the catalog copies the server's list.
  const EVENTS: Record<string, { name: string; when: string }> = {
    disk_warning: { name: "notifications.events.diskWarning.name", when: "notifications.events.diskWarning.when" },
    smart_error: { name: "notifications.events.smartError.name", when: "notifications.events.smartError.when" },
    pool_degraded: { name: "notifications.events.poolDegraded.name", when: "notifications.events.poolDegraded.when" },
    backup_complete: {
      name: "notifications.events.backupComplete.name",
      when: "notifications.events.backupComplete.when",
    },
    backup_failed: { name: "notifications.events.backupFailed.name", when: "notifications.events.backupFailed.when" },
    container_stopped: {
      name: "notifications.events.containerStopped.name",
      when: "notifications.events.containerStopped.when",
    },
    system_boot: { name: "notifications.events.systemBoot.name", when: "notifications.events.systemBoot.when" },
    user_login: { name: "notifications.events.userLogin.name", when: "notifications.events.userLogin.when" },
  };

  const own = (object: object, key: string) => Object.prototype.hasOwnProperty.call(object, key);

  let logs: NotificationLog[] = [];
  let configs: NotificationConfig[] = [];
  let eventTypes: EventType[] = [];
  let loading = true;
  let loadError = "";
  let activeView: "channels" | "history" = "channels";

  // The outcome of each channel's last test, by channel id: shown under the channel.
  let results: Record<string, { success: boolean; message: string }> = {};
  let testing: string | null = null;
  let actionError = "";

  // The dialog that adds or edits a channel.
  let showModal = false;
  let editing: NotificationConfig | null = null;
  let formKind: ChannelKind = "email";
  let formName = "";
  let formEnabled = true;
  let formEvents: string[] = [];
  let saving = false;
  let saveError = "";

  let emailHost = "";
  let emailPort = "587";
  let emailUser = "";
  let emailPassword = "";
  let emailTls = true;
  let emailVerifyTls = true;
  let emailFrom = "";
  let emailTo = "";
  let telegramToken = "";
  let telegramChatId = "";
  // The URL of a webhook, Discord or Slack channel, and a webhook's method.
  let hookUrl = "";
  let hookMethod = "POST";

  let deleting: NotificationConfig | null = null;
  let deleteBusy = false;
  let deleteError = "";

  let filterChannel = "all";
  let filterStatus: "all" | "success" | "failed" = "all";
  let expandedLog: string | null = null;

  $: totalSent = logs.length;
  $: successCount = logs.filter((l) => l.success).length;
  $: failedCount = logs.filter((l) => !l.success).length;
  $: activeChannels = configs.filter((c) => c.enabled).length;
  $: recipients = emailTo.split(",").map((e) => e.trim()).filter((e) => e);
  $: port = Number(emailPort);
  // Plain SMTP: no TLS, so no certificate to check, and the password crosses the network as it is.
  $: plainSmtp = !emailTls && port !== 465;
  $: formComplete =
    formKind === "email"
      ? emailHost.trim() !== "" && Number.isInteger(port) && port > 0 && port < 65536 &&
        emailFrom.trim() !== "" && recipients.length > 0
      : formKind === "telegram"
        ? telegramToken.trim() !== "" && telegramChatId.trim() !== ""
        : hookUrl.trim() !== "";
  $: filteredLogs = logs.filter((log) => {
    if (filterChannel !== "all" && log.channel !== filterChannel) return false;
    if (filterStatus === "success" && !log.success) return false;
    if (filterStatus === "failed" && log.success) return false;
    return true;
  });
  $: hookField = HOOK_FIELDS[formKind === "discord" || formKind === "slack" ? formKind : "webhook"];
  $: channelOptions = [
    { value: "all", label: $t("notifications.history.allChannels") },
    ...KIND_ORDER.map((kind) => ({ value: kind, label: kindName(kind) })),
  ];

  // The texts that the markup gets from functions are reactive, so that they follow a change of the
  // language.

  /** The text of `value`'s key in `keys`, in the language of the page; a value without one as it comes. */
  $: labelOf = (keys: Record<string, string>, value: string): string => (own(keys, value) ? $t(keys[value]) : value);

  /** The name of a kind of channel in the language of the page; a kind without one as it comes. */
  $: kindName = (kind: string): string => (own(KINDS, kind) ? $t(KINDS[kind as ChannelKind].name) : kind);

  $: nameOf = (config: NotificationConfig): string => config.name || kindName(config.channel);

  /** The name of an event of a channel in the language of the page; an id without one as it comes. */
  $: eventName = (id: string): string => (own(EVENTS, id) ? $t(EVENTS[id].name) : id);

  $: hostOf = (url: string): string => {
    try {
      return new URL(url).host;
    } catch {
      return $t("notifications.summary.invalidUrl");
    }
  };

  // What the list shows of a channel; webhook URLs carry their secret in the path, so only the host.
  $: summaryOf = (config: NotificationConfig): string => {
    const email = config.email_config;
    if (config.channel === "email" && email) {
      return $t("notifications.summary.email", {
        recipients: email.to_addresses.join(", "),
        host: email.smtp_host,
        port: email.smtp_port,
      });
    }
    if (config.channel === "telegram" && config.telegram_config) {
      return $t("notifications.summary.telegram", { chat: config.telegram_config.chat_id });
    }
    if (config.channel === "webhook" && config.webhook_config) {
      return $t("notifications.summary.webhook", {
        method: config.webhook_config.method,
        host: hostOf(config.webhook_config.url),
      });
    }
    const hook = config.channel === "discord" ? config.discord_config : config.slack_config;
    return hook ? $t("notifications.summary.hook", { host: hostOf(hook.webhook_url) }) : "";
  };

  // The time of a notification sent: how long ago, within a day; else its date in the language of the page.
  $: formatDate = (sentAt: string): string => {
    const date = apiTime(sentAt);
    if (!date) return sentAt;
    const diff = Date.now() - date.getTime();
    if (diff < 60000) return $t("notifications.history.justNow");
    if (diff < 3600000) return $t("notifications.history.minutesAgo", { minutes: Math.floor(diff / 60000) });
    if (diff < 86400000) return $t("notifications.history.hoursAgo", { hours: Math.floor(diff / 3600000) });
    return date.toLocaleDateString($intlLocale);
  };

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
    editing = null;
    formKind = kind;
    formName = $t("notifications.form.defaultName", { kind: kindName(kind) });
    formEnabled = true;
    formEvents = [];
    emailHost = "";
    emailPort = "587";
    emailUser = "";
    emailPassword = "";
    emailTls = true;
    emailVerifyTls = true;
    emailFrom = "";
    emailTo = "";
    telegramToken = "";
    telegramChatId = "";
    hookUrl = "";
    hookMethod = "POST";
    saveError = "";
    showModal = true;
  }

  function openEdit(config: NotificationConfig) {
    openCreate(config.channel);
    editing = config;
    formName = config.name;
    formEnabled = config.enabled;
    formEvents = [...(config.events || [])];
    // Only the settings of the channel's own kind.
    const email = config.channel === "email" ? config.email_config : undefined;
    if (email) {
      emailHost = email.smtp_host;
      emailPort = String(email.smtp_port);
      emailUser = email.smtp_user;
      emailPassword = email.smtp_password;
      emailTls = email.smtp_tls;
      emailVerifyTls = email.smtp_verify_tls;
      emailFrom = email.from_address;
      emailTo = email.to_addresses.join(", ");
    }
    if (config.channel === "telegram" && config.telegram_config) {
      telegramToken = config.telegram_config.bot_token;
      telegramChatId = config.telegram_config.chat_id;
    }
    if (config.channel === "webhook" && config.webhook_config) {
      hookUrl = config.webhook_config.url;
      // The API stores the method as it was given, and the sender reads it in any case.
      hookMethod = config.webhook_config.method.toUpperCase() === "PUT" ? "PUT" : "POST";
    }
    if (config.channel === "discord" && config.discord_config) hookUrl = config.discord_config.webhook_url;
    if (config.channel === "slack" && config.slack_config) hookUrl = config.slack_config.webhook_url;
  }

  function closeModal() {
    if (saving) return;
    showModal = false;
    editing = null;
  }

  // The channel as the form describes it. Settings that only the API sets (a webhook's headers and
  // body, Discord's and Slack's names and icons) stay as they were.
  function formConfig(): NotificationConfig {
    const kept = editing?.channel === formKind ? editing : null;
    return {
      name: formName.trim(),
      channel: formKind,
      enabled: formEnabled,
      events: formEvents,
      email_config: formKind === "email" ? {
        smtp_host: emailHost.trim(),
        smtp_port: port,
        smtp_user: emailUser,
        smtp_password: emailPassword,
        smtp_tls: emailTls,
        smtp_verify_tls: emailVerifyTls,
        from_address: emailFrom.trim(),
        to_addresses: recipients,
      } : undefined,
      telegram_config: formKind === "telegram"
        ? { bot_token: telegramToken.trim(), chat_id: telegramChatId.trim() }
        : undefined,
      webhook_config: formKind === "webhook"
        ? { ...kept?.webhook_config, url: hookUrl.trim(), method: hookMethod === "PUT" ? "PUT" : "POST" }
        : undefined,
      discord_config: formKind === "discord" ? { ...kept?.discord_config, webhook_url: hookUrl.trim() } : undefined,
      slack_config: formKind === "slack" ? { ...kept?.slack_config, webhook_url: hookUrl.trim() } : undefined,
    };
  }

  async function handleSave() {
    if (saving || !formComplete) return;
    saving = true;
    saveError = "";
    try {
      if (editing?.id) {
        await updateConfig(editing.id, formConfig());
        // A test of the old settings says nothing about the new ones.
        dismissResult(editing.id);
      } else {
        await createConfig(formConfig());
      }
      saving = false;
      closeModal();
      await loadData();
    } catch (e) {
      saveError = messageOf(e);
    } finally {
      saving = false;
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

  function askDelete(config: NotificationConfig) {
    deleting = config;
    deleteError = "";
  }

  function closeDelete() {
    if (!deleteBusy) deleting = null;
  }

  async function confirmDelete() {
    const id = deleting?.id;
    if (!id || deleteBusy) return;
    deleteBusy = true;
    try {
      await deleteConfig(id);
      dismissResult(id);
      deleteBusy = false;
      deleting = null;
      await loadData();
    } catch (e) {
      deleteError = messageOf(e);
    } finally {
      deleteBusy = false;
    }
  }

  function toggleEvent(eventId: string) {
    formEvents = formEvents.includes(eventId) ? formEvents.filter((e) => e !== eventId) : [...formEvents, eventId];
  }

  function iconOf(type: string): ComponentType {
    if (type === "error" || type === "warning") return AlertTriangle;
    if (type === "success") return CheckCircle;
    return Info;
  }

  function colorOf(type: string): string {
    if (type === "error") return "text-error";
    if (type === "warning") return "text-warning";
    if (type === "success") return "text-success";
    return "text-info";
  }
</script>

<div class="space-y-6">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <Card>
      <div class="flex items-center gap-3">
        <Bell class="w-5 h-5 text-info" />
        <div>
          <p class="text-2xl font-bold">{activeChannels}</p>
          <p class="text-xs text-muted">{$t("notifications.stats.active")}</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <Send class="w-5 h-5 text-info" />
        <div>
          <p class="text-2xl font-bold">{totalSent}</p>
          <p class="text-xs text-muted">{$t("notifications.stats.sent")}</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <CheckCircle class="w-5 h-5 text-success" />
        <div>
          <p class="text-2xl font-bold">{successCount}</p>
          <p class="text-xs text-muted">{$t("notifications.delivered")}</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <AlertTriangle class="w-5 h-5 text-error" />
        <div>
          <p class="text-2xl font-bold">{failedCount}</p>
          <p class="text-xs text-muted">{$t("notifications.failed")}</p>
        </div>
      </div>
    </Card>
  </div>

  <div class="flex items-center justify-between gap-3">
    <div class="flex gap-2">
      <Button
        variant={activeView === "channels" ? "primary" : "secondary"}
        icon={Settings}
        pressed={activeView === "channels"}
        on:click={() => (activeView = "channels")}
      >
        {$t("notifications.views.channels")}
      </Button>
      <Button
        variant={activeView === "history" ? "primary" : "secondary"}
        icon={Clock}
        pressed={activeView === "history"}
        on:click={() => (activeView = "history")}
      >
        {$t("notifications.views.history")}
      </Button>
    </div>
    <Button variant="ghost" size="icon" icon={RefreshCw} loading={loading} on:click={loadData}>
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
      <Alert variant="error" dismissible on:dismiss={() => (actionError = "")}>{actionError}</Alert>
    </div>
  {/if}

  {#if activeView === "channels"}
    <div class="space-y-4">
      <div class="flex flex-wrap gap-3">
        {#each KIND_ORDER as kind}
          <Button variant="secondary" icon={KINDS[kind].icon} on:click={() => openCreate(kind)}>
            {$t(KINDS[kind].add)}
          </Button>
        {/each}
      </div>

      {#if loading && configs.length === 0}
        <p class="loading-text">{$t("common.loading")}</p>
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
            {@const id = config.id ?? ""}
            {@const result = results[id]}
            {@const name = nameOf(config)}
            <li>
              <Card>
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <div class="flex items-center gap-4 min-w-0">
                    <svelte:component
                      this={KINDS[config.channel].icon}
                      class="w-6 h-6 flex-shrink-0 {config.enabled ? 'text-info' : 'text-muted'}"
                    />
                    <div class="min-w-0">
                      <div class="flex flex-wrap items-center gap-2">
                        <h3 class="font-medium">{name}</h3>
                        <Badge variant="neutral">{kindName(config.channel)}</Badge>
                        {#if !config.enabled}
                          <Badge variant="warning">{$t("notifications.channels.disabled")}</Badge>
                        {/if}
                      </div>
                      <p class="text-sm text-secondary break-all">{summaryOf(config)}</p>
                      {#if config.events && config.events.length > 0}
                        <div class="flex flex-wrap gap-1 mt-2">
                          {#each config.events.slice(0, 3) as event}
                            <Badge variant="neutral">{eventName(event)}</Badge>
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
                      on:click={() => handleTest(config)}
                    >
                      {$t("notifications.channels.sendTest")}<span class="sr-only"> {$t("notifications.channels.sendTestTo", { name })}</span>
                    </Button>
                    <!-- Not while the channel's test runs: its outcome would show under the new settings. -->
                    <Button variant="ghost" size="sm" disabled={testing === id} on:click={() => toggleEnabled(config)}>
                      {$t(config.enabled ? "notifications.channels.disable" : "notifications.channels.enable")}<span class="sr-only"> {$t("notifications.channels.named", { name })}</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon={Pencil}
                      disabled={testing === id}
                      on:click={() => openEdit(config)}
                    >
                      <span class="sr-only">{$t("notifications.channels.edit", { name })}</span>
                    </Button>
                    <Button variant="ghost" size="icon-sm" icon={Trash2} on:click={() => askDelete(config)}>
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
                      on:dismiss={() => dismissResult(id)}
                    >
                      <span class="break-words">{result.message}</span>
                    </Alert>
                  </div>
                {/if}
              </Card>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}

  {#if activeView === "history"}
    <div class="space-y-4">
      <div class="flex flex-wrap items-end gap-3">
        <Select
          class="w-48"
          label={$t("notifications.history.channel")}
          bind:value={filterChannel}
          options={channelOptions}
        />
        <div class="flex gap-2" role="group" aria-label={$t("notifications.history.status")}>
          <Button
            variant={filterStatus === "all" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "all"}
            on:click={() => (filterStatus = "all")}
          >
            {$t("notifications.history.all")}
          </Button>
          <Button
            variant={filterStatus === "success" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "success"}
            on:click={() => (filterStatus = "success")}
          >
            {$t("notifications.delivered")}
          </Button>
          <Button
            variant={filterStatus === "failed" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "failed"}
            on:click={() => (filterStatus = "failed")}
          >
            {$t("notifications.failed")}
          </Button>
        </div>
      </div>

      <Card>
        {#if loading && logs.length === 0}
          <p class="loading-text">{$t("common.loading")}</p>
        {:else if filteredLogs.length === 0}
          <EmptyState
            icon={Bell}
            title={$t("notifications.history.none")}
            description={$t(logs.length > 0 ? "notifications.history.noMatch" : "notifications.history.noneHint")}
          />
        {:else}
          <ul class="divide-y divide-border" aria-label={$t("notifications.history.list")}>
            {#each filteredLogs as log (log.id)}
              {@const expanded = expandedLog === log.id}
              <li class="py-3">
                <Button variant="plain"
                  type="button"
                  class="w-full text-left"
                  aria-expanded={expanded}
                  on:click={() => (expandedLog = expanded ? null : log.id)}
                >
                  <span class="flex items-start gap-3">
                    <svelte:component
                      this={iconOf(log.event.type)}
                      class="w-5 h-5 mt-0.5 flex-shrink-0 {colorOf(log.event.type)}"
                    />
                    <span class="flex-1 min-w-0">
                      <span class="flex flex-wrap items-center gap-2">
                        <span class="font-medium">{log.event.title}</span>
                        <Badge variant="neutral">{kindName(log.channel)}</Badge>
                        {#if log.channel_name}
                          <span class="text-sm text-secondary">{log.channel_name}</span>
                        {/if}
                        {#if log.success}
                          <Badge variant="success">{$t("notifications.delivered")}</Badge>
                        {:else}
                          <Badge variant="error">{$t("notifications.failed")}</Badge>
                        {/if}
                      </span>
                      <span class="block text-sm text-muted truncate mt-1">{log.event.message}</span>
                    </span>
                    <span class="text-sm text-muted flex-shrink-0">{formatDate(log.sent_at)}</span>
                    {#if expanded}
                      <ChevronUp class="w-4 h-4 mt-0.5 text-muted flex-shrink-0" />
                    {:else}
                      <ChevronDown class="w-4 h-4 mt-0.5 text-muted flex-shrink-0" />
                    {/if}
                  </span>
                </Button>
                {#if expanded}
                  <dl class="mt-3 grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <dt class="text-muted">{$t("notifications.history.sentAt")}</dt>
                      <dd>{formatDateTime(log.sent_at, $intlLocale)}</dd>
                    </div>
                    <div>
                      <dt class="text-muted">{$t("notifications.history.status")}</dt>
                      <dd class={log.success ? "text-success" : "text-error"}>
                        {$t(log.success ? "notifications.delivered" : "notifications.failed")}
                      </dd>
                    </div>
                    <div>
                      <dt class="text-muted">{$t("notifications.history.type")}</dt>
                      <dd>{labelOf(TYPES, log.event.type)}</dd>
                    </div>
                    <div>
                      <dt class="text-muted">{$t("notifications.history.channel")}</dt>
                      <dd>{log.channel_name || kindName(log.channel)}</dd>
                    </div>
                    {#if log.error}
                      <div class="col-span-2">
                        <dt class="text-muted">{$t("notifications.history.error")}</dt>
                        <dd class="mono-xs text-error break-words">{log.error}</dd>
                      </div>
                    {/if}
                  </dl>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </Card>
    </div>
  {/if}
</div>

<Modal
  open={showModal}
  title={$t(editing ? "notifications.form.editTitle" : "notifications.form.addTitle", { kind: kindName(formKind) })}
  size="lg"
  closable={!saving}
  on:close={closeModal}
>
  <div class="space-y-4">
    <Input
      label={$t("notifications.form.name")}
      bind:value={formName}
      placeholder={$t("notifications.form.namePlaceholder")}
    />

    {#if formKind === "email"}
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Input
          class="sm:col-span-2"
          label={$t("notifications.email.host")}
          bind:value={emailHost}
          placeholder={EXAMPLES.host}
          required
        />
        <Input label={$t("notifications.email.port")} type="number" bind:value={emailPort} placeholder="587" required />
      </div>
      <div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Input label={$t("common.username")} bind:value={emailUser} placeholder={EXAMPLES.user} autocomplete="off" />
          <Input
            label={$t("common.password")}
            type="password"
            bind:value={emailPassword}
            autocomplete="new-password"
          />
        </div>
        <p class="form-hint">{$t("notifications.email.loginHint")}</p>
      </div>
      <Input
        label={$t("notifications.email.from")}
        type="email"
        bind:value={emailFrom}
        placeholder={EXAMPLES.from}
        required
      />
      <Input
        label={$t("notifications.email.to")}
        bind:value={emailTo}
        placeholder={EXAMPLES.to}
        hint={$t("notifications.email.toHint")}
        required
      />
      <div class="space-y-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="form-checkbox" bind:checked={emailTls} />
          {$t("notifications.email.tls")}
        </label>
        <label class="flex items-center gap-2 {plainSmtp ? 'opacity-60' : 'cursor-pointer'}">
          <input
            type="checkbox"
            class="form-checkbox"
            bind:checked={emailVerifyTls}
            disabled={plainSmtp}
            aria-describedby="verify-tls-hint"
          />
          {$t("notifications.email.verify")}
        </label>
        <p id="verify-tls-hint" class="form-hint">{$t("notifications.email.verifyHint")}</p>
        {#if plainSmtp}
          <Alert variant="warning">{$t("notifications.email.plain")}</Alert>
        {/if}
      </div>
    {:else if formKind === "telegram"}
      <Input
        label={$t("notifications.telegram.token")}
        type="password"
        bind:value={telegramToken}
        placeholder={EXAMPLES.token}
        hint={$t("notifications.telegram.tokenHint")}
        autocomplete="off"
        required
      />
      <Input
        label={$t("notifications.telegram.chat")}
        bind:value={telegramChatId}
        placeholder={EXAMPLES.chat}
        hint={$t("notifications.telegram.chatHint")}
        required
      />
    {:else}
      <Input
        label={$t(hookField.labelKey)}
        bind:value={hookUrl}
        placeholder={hookField.example}
        hint={$t(hookField.hintKey)}
        autocomplete="off"
        required
      />
      {#if formKind === "webhook"}
        <Select label={$t("notifications.hook.method")} bind:value={hookMethod} options={METHODS} />
      {/if}
    {/if}

    {#if eventTypes.length > 0}
      <fieldset>
        <legend class="form-label">{$t("notifications.form.events")}</legend>
        <p class="form-hint mb-2">{$t("notifications.form.eventsHint")}</p>
        <div class="card-nested grid grid-cols-1 gap-2 max-h-48 overflow-y-auto">
          {#each eventTypes as event}
            {@const keys = own(EVENTS, event.id) ? EVENTS[event.id] : null}
            <label class="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                class="form-checkbox mt-1"
                checked={formEvents.includes(event.id)}
                on:change={() => toggleEvent(event.id)}
              />
              <span>
                <span class="block text-sm font-medium">{keys ? $t(keys.name) : event.name}</span>
                <span class="block text-xs text-muted">{keys ? $t(keys.when) : event.description}</span>
              </span>
            </label>
          {/each}
        </div>
      </fieldset>
    {/if}

    <div>
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" class="form-checkbox" bind:checked={formEnabled} />
        {$t("notifications.form.enabled")}
      </label>
      <p class="form-hint">{$t("notifications.form.enabledHint")}</p>
    </div>

    {#if saveError}
      <div role="alert">
        <Alert variant="error" title={$t("notifications.form.saveError")}>{saveError}</Alert>
      </div>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={saving} on:click={closeModal}>{$t("common.cancel")}</Button>
    <Button class="flex-1" loading={saving} disabled={!formComplete} on:click={handleSave}>
      {$t(editing ? "notifications.form.save" : "notifications.form.add")}
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={deleting !== null}
  title={$t("notifications.delete.title")}
  size="sm"
  danger
  closable={!deleteBusy}
  on:close={closeDelete}
>
  {#if deleting}
    <p>{$t("notifications.delete.text", { name: nameOf(deleting) })}</p>
  {/if}
  {#if deleteError}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={$t("notifications.delete.error")}>{deleteError}</Alert>
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={deleteBusy} on:click={closeDelete}>
      {$t("common.cancel")}
    </Button>
    <Button variant="danger" class="flex-1" loading={deleteBusy} on:click={confirmDelete}>
      {$t("notifications.delete.confirm")}
    </Button>
  </svelte:fragment>
</Modal>
