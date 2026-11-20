<script lang="ts">
  // The form of the off-device copy's target and of what it copies (ADR-0337, ADR-0422): saving
  // tests the target. The fields are those of `form`; the section saves, makes the key pair and
  // forgets the settings. Related fields go in a group with a legend, each field's label is short
  // and its unit stays beside it, and a Save that waits says for what.
  import type { OffsiteSettingsAnswer, OffsiteStatus } from "$lib/api/offsite";
  import { Alert, Button, Input } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { Key, Plus, Trash2 } from "lucide-svelte";
  import type { OffsiteForm } from "./form.svelte";
  import { MIN_PASSWORD_LENGTH } from "./offsite";

  type Props = {
    form: OffsiteForm;
    answer: OffsiteSettingsAnswer;
    running: OffsiteStatus["running"];
    saving: boolean;
    canSave: boolean;
    makingKey: boolean;
    onsave: () => void;
    onmakekey: () => void;
    onforget: () => void;
  };

  let { form, answer, running, saving, canSave, makingKey, onsave, onmakekey, onforget }: Props = $props();

  // The kinds of target, in the order of the choice.
  const KINDS = [
    { value: "disk", key: "offsite.form.disk" },
    { value: "s3", key: "offsite.form.s3" },
    { value: "rest", key: "offsite.form.rest" },
    { value: "sftp", key: "offsite.form.sftp" },
  ] as const;

  // What Save still waits for, in the words of the page: said beside it while it is disabled.
  let missing = $derived(
    new Intl.ListFormat($intlLocale, { type: "conjunction" }).format(form.missing.map((key) => $t(key)))
  );
</script>

<!-- A group of related fields, with its legend and one line of help: the place of FieldGroup. -->
{#snippet legend(title: string, hint: string)}
  <legend class="text-sm font-semibold text-content">{title}</legend>
  {#if hint}<p class="form-hint !mt-0.5">{hint}</p>{/if}
{/snippet}

<!-- The limits of a target that copies over the network: the same fields for every kind. -->
{#snippet limits()}
  <div class="grid grid-cols-2 gap-3 sm:max-w-md">
    <Input id="offsite-limit-upload" type="number" label={$t("offsite.form.limitUpload")} bind:value={form.limitUpload} />
    <Input id="offsite-limit-download" type="number" label={$t("offsite.form.limitDownload")} bind:value={form.limitDownload} />
  </div>
  <p class="form-hint">{$t("offsite.form.limitHint")}</p>
{/snippet}

<form
  class="divide-y divide-border"
  onsubmit={(e) => {
    e.preventDefault();
    onsave();
  }}
>
  <div class="pb-5"><fieldset class="space-y-4">
    {@render legend($t("offsite.form.title"), "")}
    <div class="segmented" role="radiogroup" aria-label={$t("offsite.form.kind")}>
      {#each KINDS as kind}
        <label
          class="tab-pill relative flex cursor-pointer items-center gap-2 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-brand-ring {form.kind === kind.value ? 'tab-pill-active' : ''}"
        >
          <!-- The radio covers its segment, unseen: a click anywhere on it chooses it. -->
          <input type="radio" name="offsite-kind" class="absolute inset-0 m-0 cursor-pointer appearance-none opacity-0" value={kind.value} bind:group={form.kind} />
          {$t(kind.key)}
        </label>
      {/each}
    </div>

    {#if form.kind === "disk"}
      <Input id="offsite-path" label={$t("offsite.form.path")} bind:value={form.path} hint={$t("offsite.form.pathHint")} spellcheck={false} />
    {:else if form.kind === "rest"}
      <Input id="offsite-rest-endpoint" label={$t("offsite.rest.endpoint")} bind:value={form.endpoint} hint={$t("offsite.rest.endpointHint")} spellcheck={false} />
      <Input id="offsite-rest-prefix" class="sm:max-w-md" label={$t("offsite.rest.prefix")} bind:value={form.prefix} spellcheck={false} />
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Input id="offsite-rest-user" label={$t("offsite.rest.username")} bind:value={form.username} autocomplete="off" spellcheck={false} />
        <Input
          id="offsite-rest-password"
          type="password"
          label={$t("offsite.rest.password")}
          bind:value={form.restPassword}
          autocomplete="new-password"
        />
      </div>
      {#if form.keepsRestPassword}<p class="form-hint -mt-2">{$t("offsite.rest.passwordKept")}</p>{/if}
      <div class="sm:max-w-md">
        <label for="offsite-rest-tls" class="form-label">{$t("offsite.rest.tls")}</label>
        <select id="offsite-rest-tls" class="form-select" bind:value={form.tls}>
          <option value="system">{$t("offsite.rest.tlsSystem")}</option>
          <option value="fingerprint">{$t("offsite.rest.tlsFingerprint")}</option>
          <option value="ca">{$t("offsite.rest.tlsCa")}</option>
        </select>
      </div>
      {#if form.tls === "fingerprint"}
        <Input
          id="offsite-rest-fingerprint"
          label={$t("offsite.rest.fingerprint")}
          bind:value={form.certificateSha256}
          hint={$t("offsite.rest.fingerprintHint")}
          spellcheck={false}
        />
      {:else if form.tls === "ca"}
        <div>
          <label for="offsite-rest-certificate" class="form-label">{$t("offsite.rest.certificate")}</label>
          <textarea
            id="offsite-rest-certificate"
            class="form-input font-mono text-xs"
            rows="6"
            spellcheck="false"
            bind:value={form.certificate}
            aria-describedby="offsite-rest-certificate-hint"
          ></textarea>
          <p id="offsite-rest-certificate-hint" class="form-hint">{$t("offsite.rest.certificateHint")}</p>
        </div>
      {/if}
      {@render limits()}
    {:else if form.kind === "sftp"}
      <div class="card-nested space-y-2 text-sm">
        <h5 class="font-medium">{$t("offsite.sftp.keyTitle")}</h5>
        <p class="text-content-secondary">{$t("offsite.sftp.keyIntro")}</p>
        {#if answer.sftp_public_key}
          <p class="form-label">{$t("offsite.sftp.publicKey")}</p>
          <code class="block font-mono text-xs break-all select-all" data-testid="sftp-public-key">{answer.sftp_public_key}</code>
          <p class="form-hint">{$t("offsite.sftp.publicKeyHint")}</p>
        {:else}
          <p class="text-status-warning">{$t("offsite.sftp.noKey")}</p>
        {/if}
        <Button size="sm" variant="secondary" icon={Key} loading={makingKey} disabled={running !== null} onclick={onmakekey}>
          {answer.sftp_public_key ? $t("offsite.sftp.newKey") : $t("offsite.sftp.makeKey")}
        </Button>
      </div>
      <div class="grid grid-cols-[minmax(0,1fr)_7rem] gap-3">
        <Input id="offsite-sftp-host" label={$t("offsite.sftp.host")} bind:value={form.sftpHost} spellcheck={false} />
        <Input id="offsite-sftp-port" type="number" label={$t("offsite.sftp.port")} bind:value={form.sftpPort} />
      </div>
      <Input id="offsite-sftp-user" class="sm:max-w-md" label={$t("offsite.sftp.username")} bind:value={form.username} autocomplete="off" spellcheck={false} />
      <Input id="offsite-sftp-path" label={$t("offsite.sftp.path")} bind:value={form.remotePath} hint={$t("offsite.sftp.pathHint")} spellcheck={false} />
      {#if form.pinShown}
        <div class="space-y-2 text-sm">
          <p class="break-all" data-testid="sftp-pinned">{$t("offsite.sftp.pinned", { fingerprint: answer.host_key_fingerprint ?? "" })}</p>
          <p class="form-hint">{$t("offsite.sftp.pinnedHint")}</p>
          <label class="flex items-center gap-2">
            <input type="checkbox" class="form-checkbox" bind:checked={form.trustNewHostKey} />
            {$t("offsite.sftp.trustNewKey")}
          </label>
        </div>
      {/if}
      {#if form.fingerprintShown}
        <Input
          id="offsite-sftp-fingerprint"
          label={form.pinShown ? $t("offsite.sftp.newFingerprint") : $t("offsite.sftp.fingerprint")}
          bind:value={form.hostKeyFingerprint}
          hint={$t("offsite.sftp.fingerprintHint")}
          spellcheck={false}
        />
      {/if}
      {@render limits()}
    {:else}
      <Input id="offsite-endpoint" label={$t("offsite.form.endpoint")} bind:value={form.endpoint} hint={$t("offsite.form.endpointHint")} spellcheck={false} />
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Input id="offsite-bucket" label={$t("offsite.form.bucket")} bind:value={form.bucket} spellcheck={false} />
        <Input id="offsite-prefix" label={$t("offsite.form.prefix")} bind:value={form.prefix} spellcheck={false} />
        <Input id="offsite-region" label={$t("offsite.form.region")} bind:value={form.region} spellcheck={false} />
      </div>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Input id="offsite-access-key" label={$t("offsite.form.accessKeyId")} bind:value={form.accessKeyId} autocomplete="off" spellcheck={false} />
        <Input
          id="offsite-secret-key"
          type="password"
          label={$t("offsite.form.secretKey")}
          bind:value={form.secretKey}
          autocomplete="new-password"
        />
      </div>
      {#if form.keepsSecret}<p class="form-hint -mt-2">{$t("offsite.form.secretKept")}</p>{/if}
      {@render limits()}
    {/if}
  </fieldset></div>

  <div class="py-5"><fieldset class="space-y-3 text-sm">
    {@render legend($t("offsite.form.sources"), $t("offsite.form.sourcesHint"))}
    {#each answer.pools as pool}
      <label class="flex items-center gap-2 break-all">
        <input type="checkbox" class="form-checkbox" value={pool.path} bind:group={form.poolSources} />
        <span class="font-mono text-xs">{pool.path}</span>
      </label>
    {/each}
    {#each form.extraSources as source}
      <div class="flex items-center gap-2">
        <span class="font-mono text-xs break-all flex-1 min-w-0">{source}</span>
        <Button size="icon-sm" variant="ghost-danger" icon={Trash2} title={$t("offsite.form.removeSource", { path: source })}
          onclick={() => form.removeSource(source)}>
          <span class="sr-only">{$t("offsite.form.removeSource", { path: source })}</span>
        </Button>
      </div>
    {/each}
    {#if answer.pools.length === 0 && form.extraSources.length === 0}
      <p class="text-content-muted">{$t("offsite.form.noPools")}</p>
    {/if}
    <div class="flex flex-wrap items-end gap-2">
      <div class="min-w-0 flex-1 basis-56">
        <Input id="offsite-new-source" label={$t("offsite.form.newSource")} bind:value={form.newSource} spellcheck={false} />
      </div>
      <Button variant="secondary" icon={Plus} disabled={!form.newSource.trim()} onclick={() => form.addSource()}>{$t("offsite.form.addSource")}</Button>
    </div>
  </fieldset></div>

  <div class="py-5"><fieldset class="space-y-3">
    {@render legend($t("offsite.form.encryption"), "")}
    <Alert variant="warning" size="sm">{$t("offsite.form.passwordWarning")}</Alert>
    <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <Input
        id="offsite-password"
        type="password"
        label={$t("offsite.form.password")}
        bind:value={form.password}
        autocomplete="new-password"
        error={form.passwordTooShort ? $t("offsite.form.passwordTooShort", { n: MIN_PASSWORD_LENGTH }) : ""}
      />
      <Input
        id="offsite-password-again"
        type="password"
        label={$t("offsite.form.passwordAgain")}
        bind:value={form.passwordAgain}
        autocomplete="new-password"
        error={form.passwordAgain && form.passwordsDiffer ? $t("offsite.form.passwordsDiffer") : ""}
      />
    </div>
    {#if !form.passwordTooShort && !(form.passwordAgain && form.passwordsDiffer)}
      <p class="form-hint">
        {form.keepsPassword ? $t("offsite.form.passwordKept") : $t("offsite.form.passwordNeeded", { n: MIN_PASSWORD_LENGTH })}
      </p>
    {/if}
  </fieldset></div>

  <!-- Two groups side by side from md: what restic keeps, and how much a check reads. The fields
       are compact, with short labels, so that they stand on one line. -->
  <div class="grid gap-x-10 gap-y-5 py-5 md:grid-cols-[auto_auto] md:justify-start">
    <fieldset class="space-y-3">
      {@render legend($t("offsite.form.keep"), $t("offsite.form.keepHint"))}
      <div class="flex flex-wrap gap-3">
        <Input id="offsite-keep-daily" class="w-28" type="number" label={$t("offsite.form.keepDaily")} bind:value={form.keepDaily} />
        <Input id="offsite-keep-weekly" class="w-28" type="number" label={$t("offsite.form.keepWeekly")} bind:value={form.keepWeekly} />
        <Input id="offsite-keep-monthly" class="w-28" type="number" label={$t("offsite.form.keepMonthly")} bind:value={form.keepMonthly} />
      </div>
    </fieldset>
    <fieldset class="space-y-3">
      {@render legend($t("offsite.form.check"), $t("offsite.form.checkHint"))}
      <div class="flex items-end gap-2">
        <Input id="offsite-check-percent" class="w-28" type="number" label={$t("offsite.form.checkPercent")} bind:value={form.checkPercent} />
        <span class="pb-2 text-sm text-content-secondary" aria-hidden="true">%</span>
      </div>
    </fieldset>
  </div>

  <div class="flex flex-wrap items-center gap-3 pt-5">
    <Button type="submit" loading={saving} disabled={!canSave} aria-describedby={canSave ? undefined : "offsite-save-why"}>
      {saving ? $t("offsite.form.saving") : $t("offsite.form.save")}
    </Button>
    {#if !canSave && !saving && missing}
      <p id="offsite-save-why" class="text-[13px] text-content-muted">{$t("offsite.form.needs", { what: missing })}</p>
    {/if}
    {#if answer.configured}
      <Button variant="secondary" class="sm:ml-auto" icon={Trash2} disabled={running !== null} onclick={onforget}>{$t("offsite.form.forget")}</Button>
    {/if}
  </div>
</form>
