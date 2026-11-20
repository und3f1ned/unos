<script lang="ts">
  import type { ChannelKind } from "$lib/api/notifications";
  import { Alert, Button, Input, Modal, Select } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import type { ChannelForm } from "./channelForm.svelte";
  import { EXAMPLES, KIND_ORDER, METHODS, kindName } from "./channels";

  // The dialog that adds or edits a channel: its kind (a new channel's only), the settings of its
  // kind, and whether it is on. Every enabled channel receives every alert, so no events are chosen
  //.
  type Props = {
    form: ChannelForm;
    // The name a new channel of a kind gets until it is given one.
    defaultName: (kind: ChannelKind) => string;
    onsave: () => void;
  };

  let { form, defaultName, onsave }: Props = $props();

  let kindOptions = $derived(KIND_ORDER.map((kind) => ({ value: kind, label: kindName($t, kind) })));

  // What a disabled Save waits for, in the words of the page: said beside it.
  let missing = $derived(
    new Intl.ListFormat($intlLocale, { type: "conjunction" }).format(form.missing.map((key) => $t(key)))
  );
</script>

<Modal
  open={form.open}
  title={$t(form.editing ? "notifications.form.editTitle" : "notifications.form.addTitle", { kind: kindName($t, form.kind) })}
  size="lg"
  closable={!form.saving}
  onclose={() => form.close()}
>
  <div class="space-y-4">
    {#if !form.editing}
      <Select
        label={$t("notifications.form.kind")}
        value={form.kind}
        options={kindOptions}
        onchange={(kind) => form.changeKind(kind as ChannelKind, defaultName)}
      />
    {/if}
    <Input
      label={$t("notifications.form.name")}
      bind:value={form.name}
      placeholder={$t("notifications.form.namePlaceholder")}
    />

    {#if form.kind === "email"}
      <div class="grid grid-cols-[minmax(0,1fr)_6.5rem] gap-3 items-start">
        <Input
          label={$t("notifications.email.host")}
          bind:value={form.emailHost}
          placeholder={EXAMPLES.host}
          required
        />
        <Input label={$t("notifications.email.port")} type="number" bind:value={form.emailPort} placeholder="587" required />
      </div>
      <div>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Input label={$t("common.username")} bind:value={form.emailUser} placeholder={EXAMPLES.user} autocomplete="off" />
          <Input
            label={$t("common.password")}
            type="password"
            bind:value={form.emailPassword}
            autocomplete="new-password"
          />
        </div>
        <p class="form-hint">{$t("notifications.email.loginHint")}</p>
      </div>
      <Input
        label={$t("notifications.email.from")}
        type="email"
        bind:value={form.emailFrom}
        placeholder={EXAMPLES.from}
        required
      />
      <Input
        label={$t("notifications.email.to")}
        bind:value={form.emailTo}
        placeholder={EXAMPLES.to}
        hint={$t("notifications.email.toHint")}
        required
      />
      <div class="space-y-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" class="form-checkbox" bind:checked={form.emailTls} />
          {$t("notifications.email.tls")}
        </label>
        <label class="flex items-center gap-2 {form.plainSmtp ? 'opacity-60' : 'cursor-pointer'}">
          <input
            type="checkbox"
            class="form-checkbox"
            bind:checked={form.emailVerifyTls}
            disabled={form.plainSmtp}
            aria-describedby="verify-tls-hint"
          />
          {$t("notifications.email.verify")}
        </label>
        <p id="verify-tls-hint" class="form-hint">{$t("notifications.email.verifyHint")}</p>
        {#if form.plainSmtp}
          <Alert variant="warning">{$t("notifications.email.plain")}</Alert>
        {/if}
      </div>
    {:else if form.kind === "telegram"}
      <Input
        label={$t("notifications.telegram.token")}
        type="password"
        bind:value={form.telegramToken}
        placeholder={EXAMPLES.token}
        hint={$t("notifications.telegram.tokenHint")}
        autocomplete="off"
        required
      />
      <Input
        label={$t("notifications.telegram.chat")}
        bind:value={form.telegramChatId}
        placeholder={EXAMPLES.chat}
        hint={$t("notifications.telegram.chatHint")}
        required
      />
    {:else}
      <Input
        label={$t(form.hookField.labelKey)}
        bind:value={form.hookUrl}
        placeholder={form.hookField.example}
        hint={$t(form.hookField.hintKey)}
        autocomplete="off"
        required
      />
      {#if form.kind === "webhook"}
        <Select label={$t("notifications.hook.method")} bind:value={form.hookMethod} options={METHODS} />
      {/if}
    {/if}

    <div>
      <label class="flex items-center gap-2 cursor-pointer">
        <input type="checkbox" class="form-checkbox" bind:checked={form.enabled} />
        {$t("notifications.form.enabled")}
      </label>
      <p class="form-hint">{$t("notifications.form.enabledHint")}</p>
    </div>

    {#if form.saveError}
      <div role="alert">
        <Alert variant="error" title={$t("notifications.form.saveError")}>{form.saveError}</Alert>
      </div>
    {/if}
    {#if !form.complete}
      <p id="channel-save-why" class="text-[13px] text-content-muted">{$t("notifications.form.needs", { what: missing })}</p>
    {/if}
  </div>

  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={form.saving} onclick={() => form.close()}>{$t("common.cancel")}</Button>
    <Button class="flex-1" loading={form.saving} disabled={!form.complete}
      aria-describedby={form.complete ? undefined : "channel-save-why"} onclick={onsave}>
      {$t(form.editing ? "notifications.form.save" : "notifications.form.add")}
    </Button>
  {/snippet}
</Modal>
