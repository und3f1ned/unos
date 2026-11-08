<script lang="ts">
  import { onMount } from "svelte";
  import type { ComponentType } from "svelte";
  import {
    AlertTriangle, Bell, CheckCircle, ChevronDown, ChevronUp, Clock, Info, Mail, MessageCircle,
    MessageSquare, Pencil, RefreshCw, Send, Settings, Slack, Trash2, Webhook, Zap
  } from "lucide-svelte";
  import { Alert, Badge, Button, Card, EmptyState, Input, Modal, Select } from "$lib/components/ui";
  import {
    createConfig, deleteConfig, getConfigs, getEventTypes, getLogs, testNotification, updateConfig,
    type ChannelKind, type EventType, type NotificationConfig, type NotificationLog
  } from "$lib/api/notifications";

  // Channels of five kinds (ADR-0260). A test reports next to its channel whether the server
  // delivered it, or why not, and the history lists every notification sent, newest first.

  const KINDS: Record<ChannelKind, { label: string; icon: ComponentType }> = {
    email: { label: "Email", icon: Mail },
    telegram: { label: "Telegram", icon: MessageCircle },
    webhook: { label: "Webhook", icon: Webhook },
    discord: { label: "Discord", icon: MessageSquare },
    slack: { label: "Slack", icon: Slack },
  };
  const KIND_ORDER: ChannelKind[] = ["email", "telegram", "webhook", "discord", "slack"];
  const HOOK_FIELDS: Record<"webhook" | "discord" | "slack", { label: string; placeholder: string; hint: string }> = {
    webhook: {
      label: "URL",
      placeholder: "https://example.com/hooks/unos",
      hint: "UNOS sends JSON with title, message, type, timestamp and source.",
    },
    discord: {
      label: "Webhook URL",
      placeholder: "https://discord.com/api/webhooks/...",
      hint: "In Discord: the channel's settings, Integrations, Webhooks, Copy Webhook URL.",
    },
    slack: {
      label: "Webhook URL",
      placeholder: "https://hooks.slack.com/services/...",
      hint: "An incoming webhook of a Slack app: it posts to the channel it was made for.",
    },
  };

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
    { value: "all", label: "All channels" },
    ...KIND_ORDER.map((kind) => ({ value: kind, label: KINDS[kind].label })),
  ];

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

  function nameOf(config: NotificationConfig): string {
    return config.name || KINDS[config.channel].label;
  }

  function hostOf(url: string): string {
    try {
      return new URL(url).host;
    } catch {
      return "an invalid URL";
    }
  }

  // What the list shows of a channel; webhook URLs carry their secret in the path, so only the host.
  function summaryOf(config: NotificationConfig): string {
    const email = config.email_config;
    if (config.channel === "email" && email) {
      return `${email.to_addresses.join(", ")} via ${email.smtp_host}:${email.smtp_port}`;
    }
    if (config.channel === "telegram" && config.telegram_config) return `Chat ID ${config.telegram_config.chat_id}`;
    if (config.channel === "webhook" && config.webhook_config) {
      return `${config.webhook_config.method} to ${hostOf(config.webhook_config.url)}`;
    }
    const hook = config.channel === "discord" ? config.discord_config : config.slack_config;
    return hook ? `Webhook at ${hostOf(hook.webhook_url)}` : "";
  }

  function openCreate(kind: ChannelKind) {
    editing = null;
    formKind = kind;
    formName = `${KINDS[kind].label} Notifications`;
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
      results = { ...results, [id]: { success: true, message: "Test notification sent" } };
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

  function formatDate(sentAt: string): string {
    const date = new Date(sentAt);
    const diff = Date.now() - date.getTime();
    if (diff < 60000) return "Just now";
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
    return date.toLocaleDateString();
  }
</script>

<div class="space-y-6">
  <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
    <Card>
      <div class="flex items-center gap-3">
        <Bell class="w-5 h-5 text-info" />
        <div>
          <p class="text-2xl font-bold">{activeChannels}</p>
          <p class="text-xs text-muted">Active Channels</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <Send class="w-5 h-5 text-info" />
        <div>
          <p class="text-2xl font-bold">{totalSent}</p>
          <p class="text-xs text-muted">Total Sent</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <CheckCircle class="w-5 h-5 text-success" />
        <div>
          <p class="text-2xl font-bold">{successCount}</p>
          <p class="text-xs text-muted">Delivered</p>
        </div>
      </div>
    </Card>
    <Card>
      <div class="flex items-center gap-3">
        <AlertTriangle class="w-5 h-5 text-error" />
        <div>
          <p class="text-2xl font-bold">{failedCount}</p>
          <p class="text-xs text-muted">Failed</p>
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
        Channels
      </Button>
      <Button
        variant={activeView === "history" ? "primary" : "secondary"}
        icon={Clock}
        pressed={activeView === "history"}
        on:click={() => (activeView = "history")}
      >
        History
      </Button>
    </div>
    <Button variant="ghost" size="icon" icon={RefreshCw} loading={loading} on:click={loadData}>
      <span class="sr-only">Refresh</span>
    </Button>
  </div>

  {#if loadError}
    <div role="alert">
      <Alert variant="error" title="The notification settings could not be read">{loadError}</Alert>
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
            Add {KINDS[kind].label}
          </Button>
        {/each}
      </div>

      {#if loading && configs.length === 0}
        <p class="loading-text">Loading...</p>
      {:else if configs.length === 0}
        <Card>
          <EmptyState
            icon={Bell}
            title="No notification channels"
            description="Add an e-mail, Telegram, webhook, Discord or Slack channel to receive notifications"
          />
        </Card>
      {:else}
        <ul class="space-y-3" aria-label="Notification channels">
          {#each configs as config (config.id)}
            {@const id = config.id ?? ""}
            {@const result = results[id]}
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
                        <h3 class="font-medium">{nameOf(config)}</h3>
                        <Badge variant="neutral">{KINDS[config.channel].label}</Badge>
                        {#if !config.enabled}
                          <Badge variant="warning">Disabled</Badge>
                        {/if}
                      </div>
                      <p class="text-sm text-secondary break-all">{summaryOf(config)}</p>
                      {#if config.events && config.events.length > 0}
                        <div class="flex flex-wrap gap-1 mt-2">
                          {#each config.events.slice(0, 3) as event}
                            <Badge variant="neutral">{event}</Badge>
                          {/each}
                          {#if config.events.length > 3}
                            <Badge variant="neutral">+{config.events.length - 3} more</Badge>
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
                      Send test<span class="sr-only"> to {nameOf(config)}</span>
                    </Button>
                    <!-- Not while the channel's test runs: its outcome would show under the new settings. -->
                    <Button variant="ghost" size="sm" disabled={testing === id} on:click={() => toggleEnabled(config)}>
                      {config.enabled ? "Disable" : "Enable"}<span class="sr-only"> {nameOf(config)}</span>
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      icon={Pencil}
                      disabled={testing === id}
                      on:click={() => openEdit(config)}
                    >
                      <span class="sr-only">Edit {nameOf(config)}</span>
                    </Button>
                    <Button variant="ghost" size="icon-sm" icon={Trash2} on:click={() => askDelete(config)}>
                      <span class="sr-only">Delete {nameOf(config)}</span>
                    </Button>
                  </div>
                </div>
                {#if result}
                  <div class="mt-3" role={result.success ? "status" : "alert"}>
                    <Alert
                      variant={result.success ? "success" : "error"}
                      title={result.success ? "" : "Test notification not sent"}
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
        <Select class="w-48" label="Channel" bind:value={filterChannel} options={channelOptions} />
        <div class="flex gap-2" role="group" aria-label="Status">
          <Button
            variant={filterStatus === "all" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "all"}
            on:click={() => (filterStatus = "all")}
          >
            All
          </Button>
          <Button
            variant={filterStatus === "success" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "success"}
            on:click={() => (filterStatus = "success")}
          >
            Delivered
          </Button>
          <Button
            variant={filterStatus === "failed" ? "primary" : "secondary"}
            size="sm"
            pressed={filterStatus === "failed"}
            on:click={() => (filterStatus = "failed")}
          >
            Failed
          </Button>
        </div>
      </div>

      <Card>
        {#if loading && logs.length === 0}
          <p class="loading-text">Loading...</p>
        {:else if filteredLogs.length === 0}
          <EmptyState
            icon={Bell}
            title="No notifications yet"
            description={logs.length > 0
              ? "No notifications match your filters"
              : "Tests, alerts and notifications sent through the API appear here"}
          />
        {:else}
          <ul class="divide-y divide-border" aria-label="Notifications sent">
            {#each filteredLogs as log (log.id)}
              {@const expanded = expandedLog === log.id}
              <li class="py-3">
                <button
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
                        <Badge variant="neutral">{KINDS[log.channel]?.label ?? log.channel}</Badge>
                        {#if log.channel_name}
                          <span class="text-sm text-secondary">{log.channel_name}</span>
                        {/if}
                        {#if log.success}
                          <Badge variant="success">Delivered</Badge>
                        {:else}
                          <Badge variant="error">Failed</Badge>
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
                </button>
                {#if expanded}
                  <dl class="mt-3 grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <dt class="text-muted">Sent at</dt>
                      <dd>{new Date(log.sent_at).toLocaleString()}</dd>
                    </div>
                    <div>
                      <dt class="text-muted">Status</dt>
                      <dd class={log.success ? "text-success" : "text-error"}>{log.success ? "Delivered" : "Failed"}</dd>
                    </div>
                    <div>
                      <dt class="text-muted">Type</dt>
                      <dd class="capitalize">{log.event.type}</dd>
                    </div>
                    <div>
                      <dt class="text-muted">Channel</dt>
                      <dd>{log.channel_name || KINDS[log.channel]?.label || log.channel}</dd>
                    </div>
                    {#if log.error}
                      <div class="col-span-2">
                        <dt class="text-muted">Error</dt>
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
  title="{editing ? 'Edit' : 'Add'} {KINDS[formKind].label} channel"
  size="lg"
  closable={!saving}
  on:close={closeModal}
>
  <div class="space-y-4">
    <Input label="Channel name" bind:value={formName} placeholder="e.g. Admin alerts" />

    {#if formKind === "email"}
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Input class="sm:col-span-2" label="SMTP host" bind:value={emailHost} placeholder="smtp.gmail.com" required />
        <Input label="Port" type="number" bind:value={emailPort} placeholder="587" required />
      </div>
      <div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Input label="Username" bind:value={emailUser} placeholder="user@example.com" autocomplete="off" />
          <Input label="Password" type="password" bind:value={emailPassword} autocomplete="new-password" />
        </div>
        <p class="form-hint">Leave both empty for a mail server that takes mail without a login, such as a relay in your network.</p>
      </div>
      <Input label="From address" type="email" bind:value={emailFrom} placeholder="noreply@example.com" required />
      <Input
        label="To addresses"
        bind:value={emailTo}
        placeholder="admin@example.com, user@example.com"
        hint="Separate several addresses with commas."
        required
      />
      <div class="space-y-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="form-checkbox" bind:checked={emailTls} />
          Use TLS/STARTTLS
        </label>
        <label class="flex items-center gap-2 {plainSmtp ? 'opacity-60' : 'cursor-pointer'}">
          <input
            type="checkbox"
            class="form-checkbox"
            bind:checked={emailVerifyTls}
            disabled={plainSmtp}
            aria-describedby="verify-tls-hint"
          />
          Verify the server's certificate
        </label>
        <p id="verify-tls-hint" class="form-hint">
          Port 465 uses TLS from the start. Turn the certificate check off only for a mail server with a
          self-signed certificate: without it, anyone on the network path can read the password.
        </p>
        {#if plainSmtp}
          <Alert variant="warning">
            Without TLS, the password and the messages cross the network unencrypted, and there is no
            certificate to check.
          </Alert>
        {/if}
      </div>
    {:else if formKind === "telegram"}
      <Input
        label="Bot token"
        type="password"
        bind:value={telegramToken}
        placeholder="123456:ABC-DEF..."
        hint="Get it from @BotFather on Telegram."
        autocomplete="off"
        required
      />
      <Input
        label="Chat ID"
        bind:value={telegramChatId}
        placeholder="-1001234567890"
        hint="Your user ID or a group's chat ID."
        required
      />
    {:else}
      <Input
        label={hookField.label}
        bind:value={hookUrl}
        placeholder={hookField.placeholder}
        hint={hookField.hint}
        autocomplete="off"
        required
      />
      {#if formKind === "webhook"}
        <Select
          label="Method"
          bind:value={hookMethod}
          options={[{ value: "POST", label: "POST" }, { value: "PUT", label: "PUT" }]}
        />
      {/if}
    {/if}

    {#if eventTypes.length > 0}
      <fieldset>
        <legend class="form-label">Notify on events</legend>
        <p class="form-hint mb-2">
          Not used yet: the pool and disk health alerts go to every enabled e-mail and Telegram channel, and
          to no other kind.
        </p>
        <div class="card-nested grid grid-cols-1 gap-2 max-h-48 overflow-y-auto">
          {#each eventTypes as event}
            <label class="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                class="form-checkbox mt-1"
                checked={formEvents.includes(event.id)}
                on:change={() => toggleEvent(event.id)}
              />
              <span>
                <span class="block text-sm font-medium">{event.name}</span>
                <span class="block text-xs text-muted">{event.description}</span>
              </span>
            </label>
          {/each}
        </div>
      </fieldset>
    {/if}

    <div>
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" class="form-checkbox" bind:checked={formEnabled} />
        Enable channel
      </label>
      <p class="form-hint">A disabled channel receives no notifications; Send test still reaches it.</p>
    </div>

    {#if saveError}
      <div role="alert">
        <Alert variant="error" title="The channel was not saved">{saveError}</Alert>
      </div>
    {/if}
  </div>

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={saving} on:click={closeModal}>Cancel</Button>
    <Button class="flex-1" loading={saving} disabled={!formComplete} on:click={handleSave}>
      {editing ? "Save Changes" : "Add Channel"}
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={deleting !== null}
  title="Delete the channel?"
  size="sm"
  danger
  closable={!deleteBusy}
  on:close={closeDelete}
>
  {#if deleting}
    <p>{nameOf(deleting)} stops receiving notifications. Its entries in the history stay.</p>
  {/if}
  {#if deleteError}
    <div role="alert" class="mt-4">
      <Alert variant="error" title="The channel was not deleted">{deleteError}</Alert>
    </div>
  {/if}

  <svelte:fragment slot="footer">
    <Button variant="secondary" class="flex-1" disabled={deleteBusy} on:click={closeDelete}>Cancel</Button>
    <Button variant="danger" class="flex-1" loading={deleteBusy} on:click={confirmDelete}>Delete</Button>
  </svelte:fragment>
</Modal>
